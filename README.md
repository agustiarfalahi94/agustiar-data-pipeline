# 🚇 Malaysia Real-Time Transit Data Pipeline

A real-time data engineering project that ingests, processes, and visualizes GTFS-Realtime (Protocol Buffers) transit data from across Malaysia.

## 🚀 The Stack
- **Engine**: Python 3.x (Managed by Rye)
- **Ingestion**: Protocol Buffers (GTFS-R) via `data.gov.my`
- **Storage**: DuckDB (In-process OLAP database)
- **Dashboard**: Streamlit (With refresh button)

## 🏗️ Architecture
1. **Extract**: Fetch binary `.pb` files from Prasarana/API.
2. **Transform**: Decode Protobuf into structured Pandas DataFrames.
3. **Load**: Upsert data into DuckDB for high-speed querying.
4. **Visualize**: Geospatial mapping with real-time fleet metrics.