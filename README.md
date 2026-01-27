# Malaysia Real-Time Transit Tracker

## Setup Instructions

### 1. Configuration File Setup

This project uses a separate configuration file to keep the code clean and to protect sensitive settings.

**First-time setup:**

```bash
# Copy the example config file
cp config.example.py config.py
```

The `config.py` file is listed in `.gitignore` and will not be committed to version control.

### 2. Customize Configuration (Optional)

Edit `config.py` to customize:

- **REGIONS**: List of transit regions to monitor
- **DATABASE_NAME**: Name of your DuckDB database file
- **TIMEZONE**: Your local timezone
- **MAP_STYLE**: Map appearance ('light', 'dark', 'streets', 'satellite')
- **ARROW_COLOR_RGB**: RGB values for arrow color `[R, G, B]`
- **DEFAULT_ZOOM**: Default map zoom level (higher = more zoomed in)
- **API_SOURCES**: Mapping of region names to API endpoints

### 3. File Structure

```
.
├── app_with_arrows.py              # Main Streamlit app (with arrow visualization)
├── ingestion_rapidbus_mrtfeeder.py # Data fetching script
├── config.py                        # Your configuration (NOT in git)
├── config.example.py                # Template config (safe to commit)
├── .gitignore                       # Git ignore rules
└── README.md                        # This file
```

### 4. What's in `.gitignore`

The following files are excluded from version control:

- `config.py` - Your personal configuration
- `*.duckdb` - Database files
- `__pycache__/` - Python cache
- `.env` - Environment variables
- IDE-specific files

### 5. Running the Application

```bash
# Install dependencies
pip install streamlit duckdb pandas pydeck gtfs-realtime-bindings

# Run the app
streamlit run app_with_arrows.py
```

### 6. Sharing Your Project

When sharing your code:

1. **DO commit**: `config.example.py`, `.gitignore`, source code
2. **DON'T commit**: `config.py`, `*.duckdb` database files

Others can set up by copying `config.example.py` to `config.py`.

## Configuration Variables Reference

| Variable | Type | Description |
|----------|------|-------------|
| `REGIONS` | list | Transit regions to monitor |
| `DATABASE_NAME` | str | DuckDB database filename |
| `DATABASE_TABLE` | str | Table name for vehicle data |
| `TIMEZONE` | str | Timezone (e.g., 'Asia/Kuala_Lumpur') |
| `DEFAULT_ZOOM` | int | Map zoom level (10-15 recommended) |
| `MAP_STYLE` | str | Map style: 'light', 'dark', 'streets' |
| `ARROW_COLOR_RGB` | list | RGB color for arrows [0-255, 0-255, 0-255] |
| `CENTER_DOT_COLOR_RGB` | list | RGB color for center dots |
| `ARROW_OPACITY` | int | Opacity (0-255) |
| `API_SOURCES` | dict | Region → API endpoint mapping |
| `REQUEST_TIMEOUT` | int | API request timeout in seconds |
| `DATA_MAX_AGE` | int | Max age of data to display (seconds) |

## Adding New Regions

To add a new transit region:

1. Edit `config.py`
2. Add region name to `REGIONS` list
3. Add API endpoint to `API_SOURCES` dictionary

Example:
```python
REGIONS = [
    'Rapid Bus KL',
    'New Region Name',  # Add here
    # ...
]

API_SOURCES = {
    'New Region Name': ['api-endpoint-here'],
    # ...
}
```