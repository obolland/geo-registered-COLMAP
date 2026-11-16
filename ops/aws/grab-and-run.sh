#!/usr/bin/env bash
# Waits for GPU capacity and runs one job on the training instance, unattended.
#   ops/aws/grab-and-run.sh <until HH:MM, local, today or tomorrow> <script> <max-minutes> <output-file>
# Every 15 minutes it asks AWS to reserve one instance of the training instance's type in its zone. A refusal
# (no capacity) costs nothing. When a reservation succeeds, the stopped instance starts into it (its
# reservation preference is "open"), run-on-gpu.sh runs the job and stops the instance, and the reservation
# is cancelled. Then it exits: one paid run at most.
# Safety nets if this Mac or process dies mid-run: the reservation is created with an end time (max-minutes
# + 30) so it expires by itself, and run-on-gpu.sh sets an in-instance shutdown timer.
set -uo pipefail

PROFILE=${GEOROUTE_AWS_PROFILE:-splat}
REGION=eu-west-2
INSTANCE=i-00779ffa487aebde0
RETRY_SECONDS=${GEOROUTE_RETRY_SECONDS:-900}

until_time=$1
script=$2
max_minutes=$3
output=$4
here=$(cd "$(dirname "$0")" && pwd)

deadline=$(date -j -f "%H:%M" "$until_time" +%s) || exit 2
((deadline > $(date +%s))) || deadline=$((deadline + 86400))
echo "trying until $(date -r "$deadline" '+%a %H:%M'), every $((RETRY_SECONDS / 60)) minutes"

aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

read -r type zone state < <(aws_ ec2 describe-instances --instance-ids "$INSTANCE" \
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
trap cancel_reservation EXIT
trap 'exit 130' INT TERM

attempt=0
while (($(date +%s) < deadline)); do
    attempt=$((attempt + 1))
    end=$(date -u -v+"$((max_minutes + 30))"M +%Y-%m-%dT%H:%M:%SZ)
    error=$(mktemp)
    id=$(aws_ ec2 create-capacity-reservation --instance-type "$type" --instance-platform Linux/UNIX \
        --availability-zone "$zone" --instance-count 1 --instance-match-criteria open \
        --end-date-type limited --end-date "$end" \
        --query CapacityReservation.CapacityReservationId --output text 2>"$error")
    if [[ $id == cr-* ]]; then
        reservation=$id
        echo "attempt $attempt at $(date +%H:%M): capacity reserved ($reservation, $type in $zone, expires $end)"
        "$here/run-on-gpu.sh" "$script" "$max_minutes" "$output"
        status=$?
        cancel_reservation
        echo "job finished with exit $status at $(date +%H:%M)"
        exit "$status"
    fi
    if ! grep -q InsufficientInstanceCapacity "$error"; then
        echo "attempt $attempt at $(date +%H:%M): unexpected error, stopping:" >&2
        cat "$error" >&2
        exit 1
    fi
    echo "attempt $attempt at $(date +%H:%M): no capacity"
    rm -f "$error"
    sleep "$RETRY_SECONDS"
done
echo "no capacity before $(date -r "$deadline" '+%a %H:%M'); nothing was run"
exit 3
