#!/usr/bin/env bash
# Waits for GPU capacity in any London zone and runs one job, unattended.
#   ops/aws/grab-and-run.sh <until HH:MM, local, today or tomorrow> <script> <max-minutes> <output-file>
# Every 15 minutes it asks AWS to reserve one instance of the training type in each zone in turn. A refusal
# (no capacity) costs nothing. On the first success:
#   - in the training instance's own zone, the stopped instance starts into the reservation (its preference
#     is "open");
#   - in another zone, a copy is launched from the training image into the reservation and stopped at once,
#     so it can be run exactly like the original;
# then run-on-gpu.sh runs the job and stops the instance, the reservation is cancelled, and it exits: one
# paid run at most.
# Safety nets if this Mac or process dies mid-run: each reservation is created with an end time
# (max-minutes + 30) so it expires by itself, and run-on-gpu.sh sets an in-instance shutdown timer.
set -uo pipefail

PROFILE=${GEOROUTE_AWS_PROFILE:-splat}
REGION=eu-west-2
INSTANCE=i-00779ffa487aebde0
IMAGE=ami-07b4055656e0194ff
SECURITY_GROUP=sg-027c5a2203a0abc9a
INSTANCE_PROFILE=georoute-splat-ec2
ZONES=(eu-west-2a eu-west-2b eu-west-2c)
declare -A SUBNETS=([eu-west-2b]=subnet-b965e2f5 [eu-west-2c]=subnet-d55e6abc)
RETRY_SECONDS=${GEOROUTE_RETRY_SECONDS:-900}

until_time=$1
script=$2
max_minutes=$3
output=$4
here=$(cd "$(dirname "$0")" && pwd)

deadline=$(date -j -f "%H:%M" "$until_time" +%s) || exit 2
((deadline > $(date +%s))) || deadline=$((deadline + 86400))
echo "trying ${ZONES[*]} until $(date -r "$deadline" '+%a %H:%M'), every $((RETRY_SECONDS / 60)) minutes"

aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

read -r type home_zone state < <(aws_ ec2 describe-instances --instance-ids "$INSTANCE" \
    --query 'Reservations[0].Instances[0].[InstanceType,Placement.AvailabilityZone,State.Name]' --output text)
[[ $state == stopped ]] || { echo "instance is $state, expected stopped" >&2; exit 1; }

reservation=""
cancel_reservation() {
    if [[ -n $reservation ]]; then
        aws_ ec2 cancel-capacity-reservation --capacity-reservation-id "$reservation" >/dev/null &&
            echo "reservation $reservation cancelled" ||
            echo "CANCEL FAILED for $reservation: it expires by itself at its end time" >&2
        reservation=""
    fi
}
launched=""
stop_launched() {
    # A copy launched but not yet handed to run-on-gpu.sh must not be left running.
    if [[ -n $launched ]]; then
        aws_ ec2 stop-instances --instance-ids "$launched" >/dev/null
        aws_ ec2 wait instance-stopped --instance-ids "$launched" && echo "copy $launched stopped"
    fi
}
cleanup() {
    stop_launched
    cancel_reservation
}
trap cleanup EXIT
trap 'exit 130' INT TERM

reserve() { # reserve <zone>: sets reservation, or returns 1 on a capacity refusal; exits on anything else
    local error id
    error=$(mktemp)
    id=$(aws_ ec2 create-capacity-reservation --instance-type "$type" --instance-platform Linux/UNIX \
        --availability-zone "$1" --instance-count 1 --instance-match-criteria open --end-date-type limited \
        --end-date "$(date -u -v+"$((max_minutes + 30))"M +%Y-%m-%dT%H:%M:%SZ)" \
        --query CapacityReservation.CapacityReservationId --output text 2>"$error")
    if [[ $id == cr-* ]]; then
        reservation=$id
        return 0
    fi
    if ! grep -q InsufficientInstanceCapacity "$error"; then
        echo "unexpected error reserving in $1, stopping:" >&2
        cat "$error" >&2
        exit 1
    fi
    return 1
}

launch_copy() { # launch_copy <zone>: launches from the image into the zone's reservation, then stops it
    local id
    id=$(aws_ ec2 run-instances --image-id "$IMAGE" --instance-type "$type" --subnet-id "${SUBNETS[$1]}" \
        --security-group-ids "$SECURITY_GROUP" --iam-instance-profile "Name=$INSTANCE_PROFILE" \
        --instance-initiated-shutdown-behavior stop \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Project,Value=georoute},{Key=Name,Value=georoute-gpu-$1}]" \
        "ResourceType=volume,Tags=[{Key=Project,Value=georoute}]" \
        --query 'Instances[0].InstanceId' --output text) || { echo "launch in $1 failed" >&2; exit 1; }
    launched=$id
    echo "launched copy $id in $1; stopping it so the job runs through run-on-gpu.sh"
    aws_ ec2 wait instance-running --instance-ids "$id"
    stop_launched
    launched=""
    target=$id
}

image_ready() {
    [[ $(aws_ ec2 describe-images --image-ids "$IMAGE" --query 'Images[0].State' --output text) == available ]]
}

attempt=0
while (($(date +%s) < deadline)); do
    attempt=$((attempt + 1))
    for zone in "${ZONES[@]}"; do
        if [[ $zone != "$home_zone" ]] && ! image_ready; then
            echo "attempt $attempt at $(date +%H:%M): $zone skipped, image not ready yet"
            continue
        fi
        reserve "$zone" || { echo "attempt $attempt at $(date +%H:%M): no capacity in $zone"; continue; }
        echo "attempt $attempt at $(date +%H:%M): capacity reserved ($reservation, $type in $zone)"
        target=$INSTANCE
        [[ $zone == "$home_zone" ]] || launch_copy "$zone"
        GEOROUTE_INSTANCE=$target "$here/run-on-gpu.sh" "$script" "$max_minutes" "$output"
        status=$?
        cancel_reservation
        echo "job finished on $target in $zone with exit $status at $(date +%H:%M)"
        exit "$status"
    done
    sleep "$RETRY_SECONDS"
done
echo "no capacity before $(date -r "$deadline" '+%a %H:%M'); nothing was run"
exit 3
