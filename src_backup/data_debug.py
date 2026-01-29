import duckdb
con = duckdb.connect('agustiar_analytics.duckdb')

# Check the raw data
print(con.execute("SELECT *, strftime(to_timestamp(timestamp::BIGINT), '%d-%m-%Y %H:%M:%S') AS time_format FROM live_buses ORDER BY timestamp DESC LIMIT 5").df())

# Check the max timestamp
print(con.execute("SELECT MAX(timestamp) FROM live_buses").fetchone())

# Check the region ingested
print(con.execute("SELECT DISTINCT region FROM live_buses").df())

con.close()