#!/usr/bin/env bash
# Runs one script on the training GPU instance through SSM and always stops the instance afterwards.
#   ops/aws/run-on-gpu.sh <script> <max-minutes> <output-file>
# Safety: refuses unless the instance is stopped and its in-instance shutdown behaviour is "stop" (never
# terminate); sets an in-instance shutdown timer (max-minutes + 10) in case this Mac loses the session;
# stops the instance on every exit path, including errors and Ctrl-C, and waits until it is stopped.
set -euo pipefail

PROFILE=${GEOROUTE_AWS_PROFILE:-splat}
REGION=eu-west-2
INSTANCE=${GEOROUTE_INSTANCE:-i-04f3833f3a68627d6}
BUCKET=georoute-splat-data-086341361260

script=$1
max_minutes=$2
output=$3
[[ -f $script ]] || { echo "no such script: $script" >&2; exit 2; }
[[ $max_minutes =~ ^[0-9]+$ && $max_minutes -ge 5 && $max_minutes -le 240 ]] || {
    echo "max-minutes must be 5-240" >&2
    exit 2
}

aws_() { aws --profile "$PROFILE" --region "$REGION" "$@"; }

state=$(aws_ ec2 describe-instances --instance-ids "$INSTANCE" --query 'Reservations[0].Instances[0].State.Name' --output text)
[[ $state == stopped ]] || { echo "instance is $state, expected stopped; not touching it" >&2; exit 1; }
behaviour=$(aws_ ec2 describe-instance-attribute --instance-id "$INSTANCE" --attribute instanceInitiatedShutdownBehavior \
    --query 'InstanceInitiatedShutdownBehavior.Value' --output text)
[[ $behaviour == stop ]] || { echo "shutdown behaviour is $behaviour, not stop; refusing" >&2; exit 1; }

started=false
stop_instance() {
    status=$?
    echo "stopping $INSTANCE"
    aws_ ec2 stop-instances --instance-ids "$INSTANCE" >/dev/null || echo "STOP CALL FAILED: stop it in the console now" >&2
    aws_ ec2 wait instance-stopped --instance-ids "$INSTANCE" && echo "instance stopped"
    if $started; then
        echo "billed time about $(((SECONDS - started_at + 59) / 60)) minutes"
    fi
    exit "$status"
}
trap stop_instance EXIT
trap 'exit 130' INT TERM

echo "starting $INSTANCE (max $max_minutes minutes)"
aws_ ec2 start-instances --instance-ids "$INSTANCE" >/dev/null
started=true
started_at=$SECONDS
aws_ ec2 wait instance-running --instance-ids "$INSTANCE"
echo "running; waiting for SSM"
for _ in $(seq 1 60); do
    ping=$(aws_ ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE" \
        --query 'InstanceInformationList[0].PingStatus' --output text)
    [[ $ping == Online ]] && break
    sleep 10
done
[[ $ping == Online ]] || { echo "SSM never came online" >&2; exit 1; }

encoded=$(base64 <"$script" | tr -d '\n')
commands=$(python3 -c 'import json, sys; print(json.dumps({"executionTimeout": [sys.argv[1]], "commands": sys.argv[2:]}))' \
    "$((max_minutes * 60))" \
    "shutdown -h +$((max_minutes + 10))" \
    "echo $encoded | base64 -d >/tmp/georoute_job.sh" \
    "bash /tmp/georoute_job.sh" \
    "shutdown -c")
command_id=$(aws_ ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
    --parameters "$commands" --timeout-seconds 600 \
    --cloud-watch-output-config CloudWatchOutputEnabled=false \
    --output-s3-bucket-name "$BUCKET" --output-s3-key-prefix "ssm-output" \
    --query Command.CommandId --output text)
[[ -n $command_id ]] || { echo "send-command failed" >&2; exit 1; }
echo "command $command_id sent"

deadline=$((SECONDS + max_minutes * 60 + 120))
while ((SECONDS < deadline)); do
    sleep 20
    status=$(aws_ ssm get-command-invocation --command-id "$command_id" --instance-id "$INSTANCE" \
        --query Status --output text 2>/dev/null || echo Pending)
    [[ $status == Pending || $status == InProgress || $status == Delayed ]] || break
done
echo "command status: $status"
aws_ ssm get-command-invocation --command-id "$command_id" --instance-id "$INSTANCE" \
    --query StandardOutputContent --output text >"$output"
echo "output (first 24k characters; full copy in s3://$BUCKET/ssm-output/$command_id/) written to $output"
[[ $status == Success ]]
