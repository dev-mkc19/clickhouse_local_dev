FROM clickhouse/clickhouse-server:25.2

RUN apt update && \
    apt install -y python3 python3-pip git

RUN pip install dbt-clickhouse==1.10.1