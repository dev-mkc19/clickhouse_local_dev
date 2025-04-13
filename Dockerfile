FROM clickhouse/clickhouse-server:25.2

RUN apt update && \
    apt install -y python3 python3-pip git

RUN pip install git+https://git.indels.tech/Data/dbt-clickhouse.git@1.5.9