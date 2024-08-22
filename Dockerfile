FROM clickhouse/clickhouse-server:24.2

RUN apt update && \
    apt install -y python3 python3-pip git

RUN pip install git+https://git.indels.tech/Data/dbt-clickhouse