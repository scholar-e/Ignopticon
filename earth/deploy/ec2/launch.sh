#!/usr/bin/env bash
# Creates the EC2 server for the web app, once: SSH key, firewall (security group), an Ubuntu 24.04 instance with
# Docker, and a fixed public IP (Elastic IP). Needs the AWS CLI configured (`aws configure`). Run it again and it
# reports the existing server instead of making a second one. Then run deploy.sh with the address it prints.
#
# Settings (environment variables): AWS_REGION (default: the CLI's region, else us-west-2), INSTANCE_TYPE
# (t4g.small, Graviton/arm64; an x86 type such as t3.small also works), DISK_GB (20), NAME (ignopticon).
set -euo pipefail

NAME=${NAME:-ignopticon}
INSTANCE_TYPE=${INSTANCE_TYPE:-t4g.small}
DISK_GB=${DISK_GB:-20}
export AWS_REGION=${AWS_REGION:-$(aws configure get region || true)}
export AWS_REGION=${AWS_REGION:-us-west-2}
KEY_FILE=${KEY_FILE:-$HOME/.ssh/$NAME-ec2.pem}
HERE=$(cd "$(dirname "$0")" && pwd)

say() { printf '\n== %s\n' "$*"; }

say "Account $(aws sts get-caller-identity --query Account --output text), region $AWS_REGION"

existing=$(aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress]' --output text)
if [[ -n "$existing" ]]; then
    say "A server named $NAME already exists (id, state, IP):"
    echo "$existing"
    exit 0
fi

# SSH key: AWS keeps the public half, the private half is saved here and nowhere else.
if aws ec2 describe-key-pairs --key-names "$NAME" >/dev/null 2>&1; then
    [[ -f "$KEY_FILE" ]] || { echo "Key pair $NAME exists in AWS but $KEY_FILE is missing: delete the key pair or set KEY_FILE." >&2; exit 1; }
else
    say "Creating SSH key pair $NAME -> $KEY_FILE"
    mkdir -p "$(dirname "$KEY_FILE")"
    aws ec2 create-key-pair --key-name "$NAME" --key-type ed25519 --query KeyMaterial --output text > "$KEY_FILE"
    chmod 400 "$KEY_FILE"
fi

# Firewall: web traffic from anywhere, SSH only from this machine's current IP.
vpc=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
[[ "$vpc" != "None" ]] || { echo "No default VPC in $AWS_REGION; create one (aws ec2 create-default-vpc) or pick another region." >&2; exit 1; }
sg=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$NAME-web" "Name=vpc-id,Values=$vpc" \
    --query 'SecurityGroups[0].GroupId' --output text)
if [[ "$sg" == "None" ]]; then
    my_ip=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
    say "Creating security group $NAME-web (80, 443 open; 22 from $my_ip only)"
    sg=$(aws ec2 create-security-group --group-name "$NAME-web" --vpc-id "$vpc" \
        --description "$NAME web server: HTTP/HTTPS from anywhere, SSH from the admin IP" --query GroupId --output text)
    aws ec2 authorize-security-group-ingress --group-id "$sg" --ip-permissions \
        "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]" \
        "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]" \
        "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$my_ip/32,Description=admin}]" >/dev/null
fi

arch=$(aws ec2 describe-instance-types --instance-types "$INSTANCE_TYPE" \
    --query 'InstanceTypes[0].ProcessorInfo.SupportedArchitectures[0]' --output text)
[[ "$arch" == "x86_64" ]] && arch=amd64
ami=$(aws ssm get-parameter --name "/aws/service/canonical/ubuntu/server/24.04/stable/current/$arch/hvm/ebs-gp3/ami-id" \
    --query Parameter.Value --output text)

say "Launching $INSTANCE_TYPE ($arch, Ubuntu 24.04 $ami, ${DISK_GB} GB disk)"
instance=$(aws ec2 run-instances --image-id "$ami" --instance-type "$INSTANCE_TYPE" --key-name "$NAME" \
    --security-group-ids "$sg" --user-data "file://$HERE/user-data.sh" \
    --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$DISK_GB,VolumeType=gp3,Encrypted=true}" \
    --metadata-options HttpTokens=required \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" "ResourceType=volume,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids "$instance"

say "Attaching a fixed public IP (Elastic IP)"
alloc=$(aws ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME}]" --query AllocationId --output text)
aws ec2 associate-address --instance-id "$instance" --allocation-id "$alloc" >/dev/null
ip=$(aws ec2 describe-addresses --allocation-ids "$alloc" --query 'Addresses[0].PublicIp' --output text)

say "Server $instance is up at $ip"
cat <<EOM
Docker installs on first boot (~2 min). Then deploy the app:

  earth/deploy/ec2/deploy.sh $ip

It will be at https://${ip//./-}.sslip.io/ (a free hostname that points at $ip). To use your own domain, add a DNS
A record for it pointing at $ip, then: earth/deploy/ec2/deploy.sh $ip your.domain
EOM
