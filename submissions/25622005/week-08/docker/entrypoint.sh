#!/bin/sh
set -eu
umask 077

# Match the N36L user's ownership so state can also be copied on the host.
nas_uid="${NAS_UID:-1000}"
nas_gid="${NAS_GID:-1000}"
for number in "$nas_uid" "$nas_gid"; do
    case "$number" in
        ''|*[!0-9]*) echo 'NAS_UID and NAS_GID must be positive integers' >&2; exit 2 ;;
    esac
    if [ "$number" -eq 0 ]; then
        echo 'Use a non-root NAS_UID and NAS_GID' >&2
        exit 2
    fi
done
if [ "$(id -g agent)" != "$nas_gid" ]; then
    groupmod --non-unique --gid "$nas_gid" agent
fi
if [ "$(id -u agent)" != "$nas_uid" ] || [ "$(id -g agent)" != "$nas_gid" ]; then
    usermod --non-unique --uid "$nas_uid" --gid "$nas_gid" agent
fi
# Bind mounts created by Docker may initially belong to root.
mkdir -p /data
chown -R agent:agent /data
exec gosu agent python /opt/deploy/launch.py "$@"
