# Docker

Docker Compose environments for the lab. The integrated network test stack is [NetRig](nw_servers/README.md).


# 最小限のクローンで利用する
```
git clone --filter=blob:none --no-checkout --depth=1 --sparse https://b.com/booyaa-nw/docker_servers.git
cd docker
git sparse-checkout set nw_servers
git checkout main
```
