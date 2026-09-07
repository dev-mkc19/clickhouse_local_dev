from confluent_kafka import SerializingProducer
from confluent_kafka.serialization import StringSerializer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer


# ============================================================
# Schema Registry
# ============================================================

schema_registry = SchemaRegistryClient({
    "url": "http://127.0.0.1:8081/apis/ccompat/v7"
})


# ============================================================
# Avro schema
# ============================================================

schema = """
{
  "type": "record",
  "name": "AnalyticsLog",
  "namespace": "analytics",
  "fields": [
    {
      "name": "event_uuid",
      "type": "string"
    },
    {
      "name": "created_at_utc",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      }
    },
    {
      "name": "service_name",
      "type": "string"
    },
    {
      "name": "event_name",
      "type": "string"
    },
    {
      "name": "user_id",
      "type": [
        "null",
        "long"
      ],
      "default": null
    },
    {
      "name": "city_id",
      "type": [
        "null",
        "long"
      ],
      "default": null
    },
    {
      "name": "payload",
      "type": "string"
    },
    {
      "name": "retention_date_utc",
      "type": {
        "type": "long",
        "logicalType": "timestamp-millis"
      }
    }
  ]
}
"""


# ============================================================
# Avro object -> dict
# ============================================================

def to_dict(obj, ctx):
    return obj


# ============================================================
# Producer
# ============================================================

def delivery_report(err, msg):
    if err is not None:
        raise RuntimeError(f"Delivery failed: {err}")
    print(f"delivered to {msg.topic()} [{msg.partition()}] offset {msg.offset()}")


producer = SerializingProducer({
    # Python запускается НА ХОСТЕ,
    # поэтому используем EXTERNAL listener.
    #
    # EXTERNAL = PLAINTEXT. 127.0.0.1, не localhost:
    # librdkafka резолвит localhost в IPv6 [::1], а Docker/Colima
    # пробрасывает порт только на IPv4.
    "bootstrap.servers": "127.0.0.1:9092",
    "broker.address.family": "v4",

    "key.serializer": StringSerializer("utf_8"),

    "value.serializer": AvroSerializer(
        schema_registry,
        schema,
        to_dict,
        conf={
            "auto.register.schemas": True
        }
    )
})


# ============================================================
# Event
# ============================================================

event = {
    "event_uuid": "550e8400-e29b-41d4-a716-446655440002",
    "created_at_utc": 1755168000000,
    "service_name": "auth",
    "event_name": "user_login",
    "user_id": 1,
    "city_id": 55,
    "payload": "{\"ip\":\"125.0.0.1\",\"device\":\"web\"}",
    "retention_date_utc": 1755772800000
}


# ============================================================
# Produce
# ============================================================

producer.produce(
    topic="analytics-logs",
    key="event-1",
    value=event,
    on_delivery=delivery_report,
)

remaining = producer.flush(10)
if remaining:
    raise RuntimeError(f"{remaining} message(s) not delivered")