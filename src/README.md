# 🚇 Malaysia Real-Time Transit Tracker

A modern, high-performance dashboard for tracking live bus positions across Malaysia with real-time updates, analytics, and interactive visualizations.

## ✨ Features

### 🗺️ Live Map
- Interactive map with directional arrows showing bus movement
- Real-time position updates
- Region filtering
- Dark/Light map themes

### 📊 Data Table
- Sortable, filterable data table
- Multi-region selection
- CSV export functionality
- Formatted timestamps and coordinates

### 📈 Analytics Dashboard
- Regional distribution charts
- Speed analysis
- Summary statistics
- Interactive Plotly visualizations

### 🎨 Customization
- Dark/Light mode for UI
- Separate dark/light theme for maps
- Manual or Auto-refresh (10s interval)
- Multi-page navigation

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/agustiar-data-pipeline.git
cd agustiar-data-pipeline

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

```bash
# Copy example config
cp config_example.py config.py

# Edit config.py if needed (optional)
```

### 3. Run the Application

```bash
streamlit run app.py
```

The dashboard will open at `http://localhost:8501`

## 📁 Project Structure

```
agustiar-data-pipeline/
├── app.py                          # Main application entry point
├── ingestion_rapidbus_mrtfeeder.py # Data fetching from GTFS API
├── config.py                       # Configuration settings
├── requirements.txt                # Python dependencies
│
├── pages/                          # Dashboard pages
│   ├── live_map.py                # Live map with arrows
│   ├── data_table.py              # Data table view
│   └── analytics.py               # Analytics charts
│
└── utils/                          # Utility modules
    ├── db.py                      # Database operations
    └── data_processor.py          # Data processing
```

## ⚙️ Configuration

Edit `config.py` to customize:

| Setting | Description | Default |
|---------|-------------|---------|
| `REGIONS` | Transit regions to monitor | All Malaysia regions |
| `DATABASE_NAME` | DuckDB database file | agustiar_analytics.duckdb |
| `TIMEZONE` | Local timezone | Asia/Kuala_Lumpur |
| `DEFAULT_ZOOM` | Map zoom level | 13 |
| `ARROW_COLOR_RGB` | Arrow color | [0, 122, 255] (Blue) |
| `DATA_MAX_AGE` | Max data age (seconds) | 3600 (1 hour) |

## 🎯 Usage Guide

### Dashboard Controls

**Sidebar Settings:**
- **Theme Toggle**: Switch between light/dark mode for UI
- **Map Theme**: Separate theme control for maps
- **Refresh Mode**: Choose Manual or Auto (10s)
- **Navigation**: Switch between Live Map, Data Table, Analytics

### Live Map View
1. Select a region from dropdown
2. View buses as directional arrows
3. Hover over arrows for vehicle details
4. Use manual refresh button (if not in auto-refresh mode)

### Data Table View
1. Select multiple regions to filter
2. View detailed vehicle data
3. Sort by any column
4. Export to CSV

### Analytics View
1. View regional distribution charts
2. Analyze speed patterns
3. Compare statistics across regions

## 🔧 Performance Optimizations

This version includes major performance improvements:

- ✅ **5x faster data loading** - Single optimized database query
- ✅ **Reduced memory usage** - Eliminated redundant data copies
- ✅ **Batch processing** - Combined multiple operations
- ✅ **Efficient filtering** - One-pass data filtering
- ✅ **Cached computations** - Metrics calculated once

## 📊 Data Sources

Data is fetched from Malaysia's official GTFS Realtime API:
- Base URL: `https://api.data.gov.my/gtfs-realtime/vehicle-position/`
- Coverage: Rapid Bus (KL, Kuantan, Penang), KTM, myBAS (14 regions)
- Update frequency: Real-time

## 🐛 Troubleshooting

**No data showing:**
- Click "Refresh Data" button
- Check internet connection
- Verify API is accessible

**Performance issues:**
- Enable auto-refresh for continuous updates
- Reduce number of selected regions in Data Table
- Clear browser cache

**Map not displaying:**
- Check map theme setting
- Verify coordinates are valid (non-zero)

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## 📝 License

This project is licensed under the MIT License.

## 🙏 Acknowledgments

- Data provided by [Malaysia's Open Data Portal](https://data.gov.my)
- Built with [Streamlit](https://streamlit.io)
- Maps powered by [Pydeck](https://deckgl.readthedocs.io)
- Charts created with [Plotly](https://plotly.com)
