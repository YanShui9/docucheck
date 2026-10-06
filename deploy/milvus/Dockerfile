FROM milvusdb/milvus:v2.6.25@sha256:29f7668e64df1c6d5cdadbfbee99f38c72e4001054f149a298725acae57230ad

# Build context is this directory, so application secrets never enter the build.
COPY embedded-etcd.yaml /milvus/configs/embedEtcd.yaml
COPY user.yaml /milvus/configs/user.yaml
