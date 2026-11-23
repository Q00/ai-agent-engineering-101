#!/bin/sh
set -eu
umask 077

# Match the NAS user's ownership so state can also be copied on the host.
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
if [ "$(id -g node)" != "$nas_gid" ]; then
    groupmod --non-unique --gid "$nas_gid" node
fi
if [ "$(id -u node)" != "$nas_uid" ] || [ "$(id -g node)" != "$nas_gid" ]; then
    usermod --non-unique --uid "$nas_uid" --gid "$nas_gid" node
fi
# Bind mounts created by NAS Docker may initially belong to root.
mkdir -p /data /home/node
chown -R node:node /data /home/node
exec gosu node python /opt/deploy/launch.py "$@"
