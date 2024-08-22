#!/bin/bash

# Define the connection parameters to the source and target systems
SRC_HOST="${CLICKHOUSE_SCR_HOST:-}"
SRC_PORT="${CLICKHOUSE_SCR_PORT:-8200}"
SRC_USER="${CLICKHOUSE_SCR_USER:-default}"
SRC_PASSWORD="${CLICKHOUSE_SCR_PASSWORD:-}"

DEST_HOST="localhost"
DEST_PORT="9000"
DEST_USER="admin"
DEST_PASSWORD="123"

# Getting a list of schemas from the source system
SCHEMAS=$(clickhouse-client --host=$SRC_HOST --port=$SRC_PORT --user=$SRC_USER --password=$SRC_PASSWORD --query="SHOW DATABASES")

if [ $? -ne 0 ]; then
    echo "Failed fetching schemas"
    # Here you can add error handling, for example, exit the script
    exit 1
fi


# Go through the list of schemes and create them on the local system
for SCHEMA in $SCHEMAS; do
    if [ "$SCHEMA" != "default" ] && [ "$SCHEMA" != "_temporary_and_external_tables" ]; then
        echo "Creating schema: $SCHEMA"
        clickhouse-client --host=$DEST_HOST --port=$DEST_PORT --user=$DEST_USER --password=$DEST_PASSWORD --query="CREATE DATABASE IF NOT EXISTS $SCHEMA ON CLUSTER '{cluster_name}'"
    fi
done

echo "Schema migration completed!"

# Calling the dbt ls --resource-type source command and getting a list of sources
DBT_SOURCES=$(cd /opt/dbt && dbt ls --resource-type source --select +detail_orders_sensitive --profiles-dir /opt/dbt --profiles-dir /opt/dbt | grep -e '^source')

# Output processing and partitioning into schema and table
for SOURCE in $DBT_SOURCES; do
    # Replacing the 'source' prefix:bi_etl.' on an empty line
    SCHEMA_AND_TABLE=${SOURCE#source:bi_etl.}

    # Separating the schema and the table
    SCHEMA=$(echo $SCHEMA_AND_TABLE | cut -d '.' -f 1)
    TABLE=$(echo $SCHEMA_AND_TABLE | cut -d '.' -f 2)

    echo "Processing Schema: $SCHEMA, Table: $TABLE"

    # Executing a query on the source system to get information about the table
    TABLE_INFO=$(clickhouse-client --host=$SRC_HOST --port=$SRC_PORT --user=$SRC_USER --password=$SRC_PASSWORD --format=LineAsString --query="SELECT create_table_query FROM remote('$SRC_HOST:$SRC_PORT', 'system.tables', '$SRC_USER', '$SRC_PASSWORD') WHERE name = '$TABLE' AND database = '$SCHEMA'")

    # Replacing {uuid} with an empty string
    MODIFIED_QUERY=$(echo "$TABLE_INFO" | sed "s/\(CREATE TABLE [a-zA-Z0-9_.]*\)/\1 on cluster '{cluster_name}'/")
    TABLE_DDL=$MODIFIED_QUERY

    # Execution of the received result on the target system
    if [ -n "$TABLE_DDL" ]; then
        echo "Creating table on target system..."
        clickhouse-client --host=$DEST_HOST --port=$DEST_PORT --user=$DEST_USER --password=$DEST_PASSWORD --query="$TABLE_DDL"

        if [ $? -ne 0 ]; then
            echo "Failed creating table: $SCHEMA, Table: $TABLE"
            # Here you can add error handling, for example, exit the script
            exit 1
        fi

        echo "Inserting sample on target system..."
        clickhouse-client --query "INSERT INTO $SCHEMA.$TABLE SELECT * FROM remote('$SRC_HOST:$SRC_PORT','$SCHEMA.$TABLE','$SRC_USER', '$SRC_PASSWORD') limit 100;"

        if [ $? -ne 0 ]; then
            echo "Failed inserting sample data into: $SCHEMA, Table: $TABLE"
            # Here you can add error handling, for example, exit the script
            exit 1
        fi

    else
        echo "No table info found for $SCHEMA.$TABLE"
    fi
done

echo "DBT sources processed!"
