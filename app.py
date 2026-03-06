import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import ta as ta_lib
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import io
import base64
import csv

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Stock Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Multi-user storage ────────────────────────────────────────────────────────
DATA_DIR = Path("portfolios")
DATA_DIR.mkdir(exist_ok=True)

def load_user_portfolio(username: str) -> dict:
    """Load user's portfolio from JSON file."""
    filepath = DATA_DIR / f"{username}.json"
    if filepath.exists():
        with open(filepath, "r") as f:
            data = json.load(f)
            # Ensure new fields exist
            if "price_alerts" not in data:
                data["price_alerts"] = []
            if "portfolio_history" not in data:
                data["portfolio_history"] = []
            if "watchlist_categories" not in data:
                data["watchlist_categories"] = {}
            return data
    return {"portfolio_raw": "", "watchlist_raw": "", "price_alerts": [], "portfolio_history": [], "watchlist_categories": {}}

def save_user_portfolio(username: str, data: dict):
    """Save user's portfolio to JSON file."""
    filepath = DATA_DIR / f"{username}.json"
    with open(filepath, "w") as f:
        json.dump(data, f)

def get_all_users() -> list:
    """Get list of all registered users."""
    return [f.stem for f in DATA_DIR.glob("*.json")]

# ── Custom CSS (dark-mode polish) ─────────────────────────────────────────────
st.markdown(
    """
    <style>
      /* Main background */
      .stApp { background-color: #0e1117; }

      /* Metric cards */
      div[data-testid="metric-container"] {
          background-color: #1c1f26;
          border: 1px solid #2e3140;
          border-radius: 12px;
          padding: 18px 24px;
      }
      div[data-testid="metric-container"] label {
          color: #8b949e !important;
          font-size: 0.80rem;
          text-transform: uppercase;
          letter-spacing: 0.08em;
      }
      div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
          font-size: 1.6rem;
          font-weight: 700;
          color: #e6edf3 !important;
      }

      /* Tab styling */
      button[data-baseweb="tab"] {
          font-size: 0.95rem;
          font-weight: 600;
          color: #8b949e;
      }
      button[data-baseweb="tab"][aria-selected="true"] {
          color: #58a6ff;
          border-bottom: 2px solid #58a6ff;
      }

      /* DataFrame */
      .stDataFrame { border-radius: 10px; overflow: hidden; }

      /* Sidebar */
      section[data-testid="stSidebar"] { background-color: #161b22; }

      /* Section headers */
      h2, h3 { color: #e6edf3; }

      /* Status badge helpers – rendered via st.markdown */
      .badge-buy  { background:#1a4731; color:#3fb950; padding:3px 10px; border-radius:20px; font-weight:700; font-size:.82rem; }
      .badge-sell { background:#4b1b1b; color:#f85149; padding:3px 10px; border-radius:20px; font-weight:700; font-size:.82rem; }
      .badge-hold { background:#2d2a1b; color:#e3b341; padding:3px 10px; border-radius:20px; font-weight:700; font-size:.82rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Download OHLCV data and compute RSI + SMA200."""
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        if df.empty:
            return pd.DataFrame()
        close = df["Close"].squeeze()
        df["SMA200"] = ta_lib.trend.sma_indicator(close, window=200)
        df["RSI"] = ta_lib.momentum.rsi(close, window=14)
        return df
    except Exception as e:
        st.warning(f"⚠️ Kan geen data ophalen voor {ticker}: {str(e)}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def get_current_price(ticker: str) -> float:
    """Return the latest closing price."""
    try:
        info = yf.Ticker(ticker).fast_info
        price = float(info.last_price)
        return price if price > 0 else 0.0
    except Exception:
        return 0.0


def calculate_fair_value(df: pd.DataFrame, current_price: float, ticker: str) -> dict:
    """Professional Fair Value calculation with reliability checks."""
    if df.empty or current_price == 0:
        return {"fair_value": current_price, "upside": 0, "valuation": "N/A", "reliability": "⚠️ No Data"}
    
    close = df["Close"]
    
    # ════════════════════════════════════════════════════════════════════════════
    # DATA QUALITY CHECKS
    # ════════════════════════════════════════════════════════════════════════════
    
    # Check 1: Minimum data points
    if len(df) < 50:
        return {"fair_value": current_price, "upside": 0, "valuation": "🟡 Fair Value", "reliability": "⚠️ Insufficient Data (<50 days)"}
    
    # Check 2: Extreme price movements (potential delisting, stock split, or error)
    price_change_1d = abs((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100) if len(df) >= 2 else 0
    if price_change_1d > 50:
        return {"fair_value": current_price, "upside": 0, "valuation": "🔴 CAUTION", "reliability": f"⚠️ Extreme move ({price_change_1d:.1f}%)"}
    
    # Check 3: Very low price stocks (penny stocks - unreliable)
    if current_price < 1:
        return {"fair_value": current_price, "upside": 0, "valuation": "🔴 CAUTION", "reliability": "⚠️ Penny Stock (unreliable)"}
    
    # ════════════════════════════════════════════════════════════════════════════
    # METHOD 1: TECHNICAL SUPPORT/RESISTANCE
    # ════════════════════════════════════════════════════════════════════════════
    weeks_52_high = close.tail(252).max() if len(df) >= 252 else close.max()
    weeks_52_low = close.tail(252).min() if len(df) >= 252 else close.min()
    
    technical_fair = (weeks_52_high + weeks_52_low) / 2
    
    # ════════════════════════════════════════════════════════════════════════════
    # METHOD 2: MOMENTUM-BASED (Mean Reversion)
    # ════════════════════════════════════════════════════════════════════════════
    sma50 = ta_lib.trend.sma_indicator(close, window=50).iloc[-1] if len(df) >= 50 else float("nan")
    sma200 = ta_lib.trend.sma_indicator(close, window=200).iloc[-1] if len(df) >= 200 else float("nan")
    
    std_dev = close.rolling(window=20).std().iloc[-1] if len(df) >= 20 else float("nan")
    
    momentum_fair = sma200 if not pd.isna(sma200) else current_price
    if not pd.isna(std_dev) and std_dev > 0:
        momentum_fair = momentum_fair * (1 + (std_dev / momentum_fair) * 0.1)
    
    # ════════════════════════════════════════════════════════════════════════════
    # METHOD 3: RELATIVE STRENGTH (Using historical average + RSI)
    # ════════════════════════════════════════════════════════════════════════════
    rsi = ta_lib.momentum.rsi(close, window=14).iloc[-1] if len(df) >= 14 else 50
    
    hist_avg = close.tail(252).mean() if len(df) >= 252 else close.mean()
    
    rsi_factor = (rsi - 50) / 100
    relative_fair = hist_avg * (1 - rsi_factor * 0.2)
    
    # ════════════════════════════════════════════════════════════════════════════
    # OUTLIER DETECTION - Filter extreme valuations
    # ════════════════════════════════════════════════════════════════════════════
    fair_values = []
    weights = []
    
    if not pd.isna(technical_fair) and technical_fair > 0:
        fair_values.append(technical_fair)
        weights.append(0.33)
    
    if not pd.isna(momentum_fair) and momentum_fair > 0:
        fair_values.append(momentum_fair)
        weights.append(0.34)
    
    if not pd.isna(relative_fair) and relative_fair > 0:
        fair_values.append(relative_fair)
        weights.append(0.33)
    
    if fair_values and sum(weights) > 0:
        fair_value = sum(v * w for v, w in zip(fair_values, weights)) / sum(weights)
    else:
        fair_value = current_price
    
    # ════════════════════════════════════════════════════════════════════════════
    # RELIABILITY ASSESSMENT
    # ════════════════════════════════════════════════════════════════════════════
    
    upside_pct = ((fair_value - current_price) / current_price * 100) if current_price > 0 else 0
    
    # Flag extreme divergences
    if abs(upside_pct) > 50:
        reliability = "⚠️ EXTREME - Verify Data"
    elif abs(upside_pct) > 30:
        reliability = "⚠️ High Uncertainty"
    elif abs(upside_pct) > 15:
        reliability = "✅ Moderate Confidence"
    else:
        reliability = "✅ Good Confidence"
    
    # ════════════════════════════════════════════════════════════════════════════
    # VALUATION ASSESSMENT (Conservative)
    # ════════════════════════════════════════════════════════════════════════════
    
    if upside_pct > 25:
        valuation = "🟢 STRONG BUY (+{:.1f}%)".format(upside_pct)
    elif upside_pct > 12:
        valuation = "🟢 Undervalued (+{:.1f}%)".format(upside_pct)
    elif upside_pct > -12:
        valuation = "🟡 Fair Value (±{:.1f}%)".format(abs(upside_pct))
    elif upside_pct > -25:
        valuation = "🔴 Overvalued ({:.1f}%)".format(upside_pct)
    else:
        valuation = "🔴 STRONG SELL ({:.1f}%)".format(upside_pct)
    
    return {
        "fair_value": fair_value,
        "upside": upside_pct,
        "valuation": valuation,
        "current_price": current_price,
        "reliability": reliability,
        "methods": {
            "technical": technical_fair,
            "momentum": momentum_fair,
            "relative": relative_fair,
        }
    }


def classify_status(price: float, sma200: float, rsi: float, df: pd.DataFrame, fair_value: float) -> str:
    """Trading signal based on Fair Value + RSI confirmation."""
    if pd.isna(price) or price == 0 or df.empty:
        return "HOLD"
    
    if pd.isna(rsi) or pd.isna(fair_value):
        return "HOLD"
    
    upside_pct = ((fair_value - price) / price * 100) if price > 0 else 0
    
    # BUY: Undervalued + Supportive RSI (not overbought)
    if upside_pct > 12 and rsi < 70:
        return "BUY"
    
    # SELL: Overvalued + Weak RSI (not oversold)
    elif upside_pct < -12 and rsi > 30:
        return "SELL"
    
    # HOLD: Everything else
    else:
        return "HOLD"


def status_badge(status: str) -> str:
    css = {"BUY": "badge-buy", "SELL": "badge-sell", "HOLD": "badge-hold"}
    return f'<span class="{css.get(status, "badge-hold")}">{status}</span>'


@st.cache_data(ttl=3600)
def fetch_ticker_info(ticker: str) -> dict:
    """Fetch detailed ticker information with fundamentals."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
        # Calculate some derived metrics
        pe_ratio = info.get("trailingPE", None)
        forward_pe = info.get("forwardPE", None)
        pb_ratio = info.get("priceToBook", None)
        
        # Earnings growth (YoY)
        earnings_growth = info.get("earningsGrowth", None)
        
        # Debt/Equity
        total_debt = info.get("totalDebt", None)
        total_equity = info.get("totalEquity", None)
        debt_to_equity = None
        if total_debt and total_equity and total_equity > 0:
            debt_to_equity = total_debt / total_equity
        
        # Profit margins
        profit_margin = info.get("profitMargins", None)
        operating_margin = info.get("operatingMargins", None)
        
        # Return on Equity (ROE)
        roe = info.get("returnOnEquity", None)
        
        # Revenue/Cash flow
        revenue_growth = info.get("revenueGrowth", None)
        free_cash_flow = info.get("operatingCashflow", None)
        
        return {
            "dividend_yield": info.get("dividendYield", 0),
            "pe_ratio": pe_ratio,
            "forward_pe": forward_pe,
            "pb_ratio": pb_ratio,
            "market_cap": info.get("marketCap", None),
            "sector": info.get("sector", "N/A"),
            "industry": info.get("industry", "N/A"),
            "52_week_high": info.get("fiftyTwoWeekHigh", None),
            "52_week_low": info.get("fiftyTwoWeekLow", None),
            # Fundamentals
            "earnings_growth": earnings_growth,
            "revenue_growth": revenue_growth,
            "profit_margin": profit_margin,
            "operating_margin": operating_margin,
            "roe": roe,
            "debt_to_equity": debt_to_equity,
            "free_cash_flow": free_cash_flow,
        }
    except Exception:
        return {
            "dividend_yield": 0,
            "pe_ratio": None,
            "forward_pe": None,
            "pb_ratio": None,
            "market_cap": None,
            "sector": "N/A",
            "industry": "N/A",
            "52_week_high": None,
            "52_week_low": None,
            "earnings_growth": None,
            "revenue_growth": None,
            "profit_margin": None,
            "operating_margin": None,
            "roe": None,
            "debt_to_equity": None,
            "free_cash_flow": None,
        }


def calculate_support_resistance(df: pd.DataFrame) -> dict:
    """Calculate support and resistance levels."""
    if df.empty or len(df) < 50:
        return {"support": None, "resistance": None, "pivot": None}
    
    close = df["Close"]
    
    # Pivot Point Method
    high = df["High"].iloc[-1]
    low = df["Low"].iloc[-1]
    close_price = df["Close"].iloc[-1]
    
    pivot = (high + low + close_price) / 3
    support1 = (2 * pivot) - high
    resistance1 = (2 * pivot) - low
    
    return {
        "pivot": pivot,
        "support1": support1,
        "resistance1": resistance1,
        "support2": pivot - (resistance1 - support1),
        "resistance2": pivot + (resistance1 - support1),
    }


def calculate_volume_trend(df: pd.DataFrame) -> str:
    """Analyze volume trend."""
    if df.empty or len(df) < 20:
        return "N/A"
    
    recent_volume = df["Volume"].tail(5).mean()
    historical_volume = df["Volume"].tail(20).mean()
    
    if recent_volume > historical_volume * 1.5:
        return "📈 High Volume (Bullish)"
    elif recent_volume < historical_volume * 0.7:
        return "📉 Low Volume (Weak)"
    else:
        return "📊 Normal Volume"


def get_market_health() -> dict:
    """Get market health indicators (VIX-like, market breadth)."""
    try:
        # VIX index (volatility)
        vix = yf.Ticker("^VIX")
        vix_data = vix.history(period="1y")
        if not vix_data.empty:
            current_vix = vix_data["Close"].iloc[-1]
            vix_20d_avg = vix_data["Close"].tail(20).mean()
            
            if current_vix < 15:
                market_regime = "🟢 Calm (Low volatility)"
            elif current_vix < 20:
                market_regime = "🟡 Normal (Moderate volatility)"
            elif current_vix < 30:
                market_regime = "🔴 Stressed (High volatility)"
            else:
                market_regime = "⚠️ Panic (Extreme volatility)"
        else:
            current_vix = float("nan")
            vix_20d_avg = float("nan")
            market_regime = "N/A"
        
        # S&P 500 trend (bull/bear indicator)
        sp500 = yf.Ticker("^GSPC")
        sp500_data = sp500.history(period="1y")
        if not sp500_data.empty:
            sp500_price = sp500_data["Close"].iloc[-1]
            sp500_sma200 = sp500_data["Close"].tail(200).mean() if len(sp500_data) >= 200 else float("nan")
            
            if not pd.isna(sp500_sma200) and sp500_price > sp500_sma200:
                bull_regime = "🟢 Bull Market"
            else:
                bull_regime = "🔴 Bear Market"
        else:
            sp500_price = float("nan")
            sp500_sma200 = float("nan")
            bull_regime = "N/A"
        
        return {
            "vix": current_vix,
            "vix_20d_avg": vix_20d_avg,
            "market_regime": market_regime,
            "bull_regime": bull_regime,
            "sp500_price": sp500_price,
            "sp500_sma200": sp500_sma200,
        }
    except Exception:
        return {
            "vix": float("nan"),
            "vix_20d_avg": float("nan"),
            "market_regime": "N/A",
            "bull_regime": "N/A",
            "sp500_price": float("nan"),
            "sp500_sma200": float("nan"),
        }


# ════════════════════════════════════════════════════════════════════════════
# NEW FEATURES: Price Alerts, Portfolio History, News, Export, etc.
# ════════════════════════════════════════════════════════════════════════════

def check_price_alerts(alerts: list, ticker_data: dict) -> list:
    """Check which price alerts have been triggered."""
    triggered = []
    for alert in alerts:
        ticker = alert.get("ticker", "")
        target_price = alert.get("target_price", 0)
        alert_type = alert.get("type", "above")  # "above" or "below"
        
        df = ticker_data.get(ticker, pd.DataFrame())
        if df.empty:
            continue
        
        current_price = df["Close"].iloc[-1]
        
        if alert_type == "above" and current_price >= target_price:
            triggered.append({
                "ticker": ticker,
                "target": target_price,
                "current": current_price,
                "type": "🟢 Above Target",
                "message": f"{ticker} has reached ${current_price:.2f} (target: ${target_price:.2f})"
            })
        elif alert_type == "below" and current_price <= target_price:
            triggered.append({
                "ticker": ticker,
                "target": target_price,
                "current": current_price,
                "type": "🔴 Below Target",
                "message": f"{ticker} has dropped to ${current_price:.2f} (target: ${target_price:.2f})"
            })
    
    return triggered


def record_portfolio_snapshot(portfolio_positions, ticker_data: dict, existing_history: list) -> list:
    """Record daily portfolio value snapshot for history tracking."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Check if we already have today's snapshot
    if existing_history and existing_history[-1].get("date") == today:
        return existing_history  # Already recorded today
    
    total_value = 0
    total_cost = 0
    
    for pos in portfolio_positions:
        df = ticker_data.get(pos["ticker"], pd.DataFrame())
        if not df.empty:
            current_price = df["Close"].iloc[-1]
            total_value += pos["qty"] * current_price
            total_cost += pos["qty"] * pos["gak"]
    
    if total_value > 0:
        existing_history.append({
            "date": today,
            "value": total_value,
            "cost": total_cost,
            "return_pct": ((total_value - total_cost) / total_cost * 100) if total_cost > 0 else 0
        })
    
    # Keep only last 365 days
    if len(existing_history) > 365:
        existing_history = existing_history[-365:]
    
    return existing_history


@st.cache_data(ttl=3600)
def fetch_stock_news(ticker: str) -> list:
    """Fetch recent news for a stock."""
    try:
        t = yf.Ticker(ticker)
        news = t.news
        if news:
            # Return top 5 news items
            return [{
                "title": n.get("title", "No title"),
                "publisher": n.get("publisher", "Unknown"),
                "link": n.get("link", "#"),
                "published": datetime.fromtimestamp(n.get("providerPublishTime", 0)).strftime("%d %b %Y") if n.get("providerPublishTime") else "Unknown",
                "type": n.get("type", "STORY")
            } for n in news[:5]]
        return []
    except Exception:
        return []


@st.cache_data(ttl=3600)
def get_earnings_calendar(ticker: str) -> dict:
    """Get upcoming earnings date for a stock."""
    try:
        t = yf.Ticker(ticker)
        calendar = t.calendar
        
        if calendar is not None and not calendar.empty:
            # calendar can be DataFrame or dict
            if isinstance(calendar, pd.DataFrame):
                earnings_date = calendar.iloc[0, 0] if calendar.shape[1] > 0 else None
            else:
                earnings_date = calendar.get("Earnings Date", [None])[0]
            
            if earnings_date:
                if isinstance(earnings_date, str):
                    return {"earnings_date": earnings_date, "days_until": "Unknown"}
                else:
                    days_until = (earnings_date - datetime.now()).days
                    return {
                        "earnings_date": earnings_date.strftime("%Y-%m-%d"),
                        "days_until": days_until
                    }
        return {"earnings_date": None, "days_until": None}
    except Exception:
        return {"earnings_date": None, "days_until": None}


def export_portfolio_csv(portfolio_positions, ticker_data: dict) -> str:
    """Export portfolio to CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["Ticker", "Quantity", "Purchase Price", "Current Price", "Current Value", "P/L ($)", "P/L (%)", "Sector"])
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        df = ticker_data.get(ticker, pd.DataFrame())
        info = fetch_ticker_info(ticker)
        
        if not df.empty:
            current_price = df["Close"].iloc[-1]
            current_value = pos["qty"] * current_price
            cost = pos["qty"] * pos["gak"]
            pl = current_value - cost
            pl_pct = (pl / cost * 100) if cost > 0 else 0
            
            writer.writerow([
                ticker,
                pos["qty"],
                f"${pos['gak']:.2f}",
                f"${current_price:.2f}",
                f"${current_value:.2f}",
                f"${pl:.2f}",
                f"{pl_pct:.2f}%",
                info.get("sector", "Unknown")
            ])
    
    return output.getvalue()


def calculate_position_size(account_value: float, risk_per_trade: float, entry_price: float, stop_loss: float) -> dict:
    """Calculate position size based on risk management."""
    if entry_price <= 0 or stop_loss <= 0:
        return {}
    
    # Risk amount
    risk_amount = account_value * (risk_per_trade / 100)
    
    # Price difference (risk per share)
    risk_per_share = abs(entry_price - stop_loss)
    
    if risk_per_share == 0:
        return {}
    
    # Number of shares
    shares = int(risk_amount / risk_per_share)
    
    # Total position value
    position_value = shares * entry_price
    
    # Position as % of portfolio
    position_pct = (position_value / account_value * 100) if account_value > 0 else 0
    
    # Kelly Criterion (simplified)
    # Assumes 50% win rate with 2:1 reward-to-risk
    win_rate = 0.50
    win_loss_ratio = 2.0
    kelly_pct = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
    kelly_shares = int((account_value * kelly_pct) / entry_price)
    
    return {
        "shares": shares,
        "position_value": position_value,
        "position_pct": position_pct,
        "risk_amount": risk_amount,
        "risk_per_share": risk_per_share,
        "kelly_shares": kelly_shares,
        "kelly_value": kelly_shares * entry_price,
    }


def build_enhanced_chart(df: pd.DataFrame, ticker: str, show_bb: bool = True, show_macd: bool = True) -> go.Figure:
    """Enhanced candlestick chart with Bollinger Bands and MACD."""
    if df.empty:
        return go.Figure()
    
    close = df["Close"]
    
    # Calculate indicators
    if show_bb:
        bb_upper = ta_lib.volatility.bollinger_hband(close, window=20)
        bb_lower = ta_lib.volatility.bollinger_lband(close, window=20)
        bb_mid = ta_lib.volatility.bollinger_mavg(close, window=20)
    
    if show_macd:
        macd = ta_lib.trend.macd(close)
        macd_signal = ta_lib.trend.macd_signal(close)
        macd_hist = ta_lib.trend.macd_diff(close)
    
    # Create subplots
    n_rows = 2 + (1 if show_macd else 0)
    row_heights = [0.5, 0.25, 0.25] if show_macd else [0.6, 0.4]
    subplot_titles = [f"{ticker} - Price", "RSI (14)"]
    if show_macd:
        subplot_titles.append("MACD")
    
    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
        subplot_titles=subplot_titles,
    )
    
    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"],
            name="Price",
            increasing_line_color="#3fb950",
            decreasing_line_color="#f85149",
        ),
        row=1, col=1,
    )
    
    # SMA200
    if "SMA200" in df.columns:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["SMA200"], name="SMA 200",
                      line=dict(color="#58a6ff", width=1.5, dash="dot")),
            row=1, col=1,
        )
    
    # Bollinger Bands
    if show_bb:
        fig.add_trace(
            go.Scatter(x=df.index, y=bb_upper, name="BB Upper",
                      line=dict(color="#bc8ef7", width=1), opacity=0.6),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=bb_lower, name="BB Lower",
                      line=dict(color="#bc8ef7", width=1), opacity=0.6,
                      fill="tonexty", fillcolor="rgba(188,142,247,0.1)"),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=bb_mid, name="BB Mid",
                      line=dict(color="#bc8ef7", width=1, dash="dash"), opacity=0.4),
            row=1, col=1,
        )
    
    # RSI
    if "RSI" in df.columns:
        fig.add_trace(
            go.Scatter(x=df.index, y=df["RSI"], name="RSI",
                      line=dict(color="#e3b341", width=1.5),
                      fill="tozeroy", fillcolor="rgba(227,179,65,0.08)"),
            row=2, col=1,
        )
        fig.add_hline(y=70, line_dash="dash", line_color="#f85149", opacity=0.5, row=2, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="#3fb950", opacity=0.5, row=2, col=1)
    
    # MACD
    if show_macd:
        fig.add_trace(
            go.Scatter(x=df.index, y=macd, name="MACD",
                      line=dict(color="#58a6ff", width=1.5)),
            row=3, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=macd_signal, name="Signal",
                      line=dict(color="#f85149", width=1.5)),
            row=3, col=1,
        )
        colors = ["#3fb950" if v >= 0 else "#f85149" for v in macd_hist]
        fig.add_trace(
            go.Bar(x=df.index, y=macd_hist, name="Histogram",
                  marker_color=colors, opacity=0.6),
            row=3, col=1,
        )
    
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        height=700 if show_macd else 550,
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        showlegend=True,
    )
    fig.update_yaxes(gridcolor="#2e3140", zerolinecolor="#2e3140")
    fig.update_xaxes(gridcolor="#2e3140", zerolinecolor="#2e3140")
    
    return fig


# ════════════════════════════════════════════════════════════════════════════
# ADVANCED FEATURES FUNCTIONS
# ════════════════════════════════════════════════════════════════════════════

def calculate_risk_metrics(portfolio_positions, ticker_data: dict) -> dict:
    """Calculate advanced risk metrics: VaR, Sharpe, Max Drawdown, Beta."""
    if not portfolio_positions:
        return {}
    
    returns_list = []
    weights = []
    total_value = 0
    
    for pos in portfolio_positions:
        df = ticker_data.get(pos["ticker"], pd.DataFrame())
        if not df.empty and len(df) >= 60:
            position_value = pos["qty"] * df["Close"].iloc[-1]
            total_value += position_value
            weights.append(position_value)
            
            # Calculate returns
            returns = df["Close"].pct_change().dropna()
            if len(returns) > 0:
                returns_list.append(returns)
    
    if not returns_list or total_value == 0:
        return {}
    
    # Normalize weights
    weights = [w / total_value for w in weights]
    
    # Combine returns into portfolio
    combined_returns = pd.concat(returns_list, axis=1).fillna(0)
    portfolio_returns = (combined_returns * weights).sum(axis=1)
    
    # VaR (95% confidence, 1-day loss)
    var_95 = portfolio_returns.quantile(0.05)
    
    # Sharpe Ratio (annualized)
    annualized_return = portfolio_returns.mean() * 252
    annualized_std = portfolio_returns.std() * np.sqrt(252)
    risk_free_rate = 0.04  # Current risk-free rate
    sharpe = (annualized_return - risk_free_rate) / annualized_std if annualized_std > 0 else 0
    
    # Max Drawdown
    cumulative = (1 + portfolio_returns).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()
    
    # Beta (vs S&P 500)
    try:
        sp500 = yf.Ticker("^GSPC")
        sp500_data = sp500.history(period="1y", progress=False)
        sp500_returns = sp500_data["Close"].pct_change().dropna()
        
        if len(sp500_returns) == len(portfolio_returns):
            covariance = portfolio_returns.cov(sp500_returns)
            sp500_variance = sp500_returns.var()
            beta = covariance / sp500_variance if sp500_variance > 0 else 0
        else:
            beta = float("nan")
    except:
        beta = float("nan")
    
    return {
        "var_95": var_95,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_drawdown,
        "beta": beta,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_std,
    }


def get_position_alerts(portfolio_positions, ticker_data: dict) -> list:
    """Generate alerts for portfolio positions."""
    alerts = []
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        df = ticker_data.get(ticker, pd.DataFrame())
        
        if df.empty:
            continue
        
        current_price = df["Close"].iloc[-1]
        purchase_price = pos["gak"]
        current_return = ((current_price - purchase_price) / purchase_price * 100)
        
        # Alert 1: Large drop (>10%)
        if current_return < -10:
            alerts.append({
                "type": "⚠️ Major Loss",
                "ticker": ticker,
                "message": f"{ticker} down {current_return:.1f}% - Consider selling",
                "severity": "high" if current_return < -20 else "medium"
            })
        
        # Alert 2: Large gain (>30%)
        if current_return > 30:
            alerts.append({
                "type": "🎯 Take Profit",
                "ticker": ticker,
                "message": f"{ticker} up {current_return:.1f}% - Consider taking profit",
                "severity": "medium"
            })
        
        # Alert 3: RSI extremes
        rsi = latest(ticker, "RSI")
        if not pd.isna(rsi):
            if rsi > 75:
                alerts.append({
                    "type": "🔥 Overbought",
                    "ticker": ticker,
                    "message": f"{ticker} RSI at {rsi:.1f} - Potential pullback",
                    "severity": "low"
                })
            elif rsi < 25:
                alerts.append({
                    "type": "❄️ Oversold",
                    "ticker": ticker,
                    "message": f"{ticker} RSI at {rsi:.1f} - Potential bounce",
                    "severity": "low"
                })
    
    return sorted(alerts, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["severity"]])


def calculate_tax_loss_harvesting(portfolio_positions, ticker_data: dict) -> list:
    """Identify positions for tax loss harvesting."""
    tax_loss_positions = []
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        df = ticker_data.get(ticker, pd.DataFrame())
        
        if df.empty:
            continue
        
        current_price = df["Close"].iloc[-1]
        purchase_price = pos["gak"]
        total_loss = pos["qty"] * (current_price - purchase_price)
        loss_pct = ((current_price - purchase_price) / purchase_price * 100)
        
        # Only include if losing money
        if total_loss < 0:
            tax_loss_positions.append({
                "ticker": ticker,
                "current_price": current_price,
                "purchase_price": purchase_price,
                "total_loss": total_loss,
                "loss_pct": loss_pct,
                "shares": pos["qty"],
                "tax_benefit": total_loss * 0.24,  # Assume 24% tax bracket
            })
    
    return sorted(tax_loss_positions, key=lambda x: x["total_loss"])


def get_rebalancing_suggestion(portfolio_positions, ticker_data: dict, target_allocation: dict = None) -> dict:
    """Suggest portfolio rebalancing."""
    if not portfolio_positions:
        return {}
    
    # Default target: 60% stocks, 40% diversified
    if target_allocation is None:
        target_allocation = {"Tech": 0.25, "Finance": 0.20, "Healthcare": 0.15, "Other": 0.40}
    
    # Current allocation
    sector_values = {}
    total_value = 0
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        df = ticker_data.get(ticker, pd.DataFrame())
        
        if df.empty:
            continue
        
        info = fetch_ticker_info(ticker)
        sector = info.get("sector", "Other")
        value = pos["qty"] * df["Close"].iloc[-1]
        
        total_value += value
        sector_values[sector] = sector_values.get(sector, 0) + value
    
    if total_value == 0:
        return {}
    
    # Calculate % allocation
    current_allocation = {k: v / total_value for k, v in sector_values.items()}
    
    # Compare to target
    rebalancing = []
    for sector, target_pct in target_allocation.items():
        current_pct = current_allocation.get(sector, 0)
        diff = current_pct - target_pct
        
        if abs(diff) > 0.05:  # Threshold 5%
            rebalancing.append({
                "sector": sector,
                "current": current_pct * 100,
                "target": target_pct * 100,
                "action": "🔴 SELL" if diff > 0 else "🟢 BUY",
                "amount": abs(diff) * total_value,
            })
    
    return {"rebalancing": rebalancing, "total_value": total_value}


def get_dividend_info(portfolio_positions, ticker_data: dict) -> dict:
    """Get dividend information for portfolio."""
    dividend_data = []
    total_dividend_yield = 0
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        info = fetch_ticker_info(ticker)
        
        dividend_yield = info.get("dividend_yield", 0)
        if dividend_yield and dividend_yield > 0:
            df = ticker_data.get(ticker, pd.DataFrame())
            if not df.empty:
                price = df["Close"].iloc[-1]
                position_value = pos["qty"] * price
                annual_dividend = position_value * dividend_yield
                
                dividend_data.append({
                    "ticker": ticker,
                    "yield": dividend_yield * 100,
                    "annual_dividend": annual_dividend,
                    "position_value": position_value,
                })
                
                total_dividend_yield += dividend_yield * position_value
    
    return {
        "dividends": dividend_data,
        "total_annual_income": total_dividend_yield,
        "count": len(dividend_data),
    }


def compare_vs_benchmark(portfolio_positions, ticker_data: dict) -> dict:
    """Compare portfolio performance vs S&P 500."""
    try:
        # Get portfolio return
        portfolio_value = sum(pos["qty"] * ticker_data.get(pos["ticker"], pd.DataFrame())["Close"].iloc[-1] 
                             for pos in portfolio_positions 
                             if not ticker_data.get(pos["ticker"], pd.DataFrame()).empty)
        portfolio_cost = sum(pos["qty"] * pos["gak"] for pos in portfolio_positions)
        portfolio_return = (portfolio_value - portfolio_cost) / portfolio_cost * 100 if portfolio_cost > 0 else 0
        
        # Get S&P 500 return (1 year)
        sp500 = yf.Ticker("^GSPC")
        sp500_hist = sp500.history(period="1y", progress=False)
        
        if not sp500_hist.empty and len(sp500_hist) > 0:
            sp500_start = sp500_hist["Close"].iloc[0]
            sp500_end = sp500_hist["Close"].iloc[-1]
            sp500_return = (sp500_end - sp500_start) / sp500_start * 100
        else:
            sp500_return = float("nan")
        
        return {
            "portfolio_return": portfolio_return,
            "sp500_return": sp500_return,
            "outperformance": portfolio_return - sp500_return if not pd.isna(sp500_return) else float("nan"),
        }
    except:
        return {}


def get_stock_recommendations(watchlist_tickers: list, portfolio_tickers: list, ticker_data: dict, popular_tickers: list = None) -> list:
    """Get enterprise-grade stock recommendations with multiple filters."""
    recommendations = []
    
    # Default popular stocks (broader selection, not just S&P 500)
    if popular_tickers is None:
        popular_tickers = [
            # Tech
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "NFLX", "AMD", "INTC",
            # Finance
            "JPM", "BAC", "GS", "WFC", "V", "MA", "AXP", "BLK", "BX",
            # Healthcare
            "JNJ", "UNH", "PFE", "MRK", "ABBV", "TMO", "LLY", "AZN",
            # Consumer
            "WMT", "MCD", "SBUX", "NKE", "HD", "COST", "CVX", "XOM",
            # Growth/Small Cap
            "SOFI", "PYPL", "SQ", "COIN", "CRWD", "ZS", "OKTA", "DDOG", "CRM", "ADBE",
            # International/ETFs
            "VTI", "VOO", "QQQ", "IVV", "SPLG", "SCHX",
            # More diverse
            "UBER", "LYFT", "DASH", "ZM", "ROKU", "SNAP", "PINTEREST", "TWILIO",
            "RBLX", "U", "PLAN", "UPST", "ABNB", "LCID", "RIVN", "PLTR"
        ]
    
    # Combine watchlist + popular stocks, remove duplicates
    # NOTE: We NO LONGER exclude portfolio holdings - users want to see all recommendations
    all_candidates = list(set(watchlist_tickers + popular_tickers + portfolio_tickers))
    
    # Sector mapping for diversification
    sector_map = {
        "AAPL": "Tech", "MSFT": "Tech", "GOOGL": "Tech", "AMZN": "Tech", "NVDA": "Tech", "META": "Tech",
        "TSLA": "Auto", "NFLX": "Media", "AMD": "Tech", "INTC": "Tech", "ADBE": "Tech", "CRM": "Tech",
        "JPM": "Finance", "BAC": "Finance", "GS": "Finance", "WFC": "Finance", "V": "Finance", "MA": "Finance",
        "AXP": "Finance", "BLK": "Finance", "BX": "Finance",
        "JNJ": "Healthcare", "UNH": "Healthcare", "PFE": "Healthcare", "MRK": "Healthcare", "ABBV": "Healthcare",
        "TMO": "Healthcare", "LLY": "Healthcare", "AZN": "Healthcare",
        "WMT": "Consumer", "MCD": "Consumer", "SBUX": "Consumer", "NKE": "Consumer", "HD": "Consumer", "COST": "Consumer",
        "CVX": "Energy", "XOM": "Energy",
        "SOFI": "Finance", "PYPL": "Finance", "SQ": "Finance", "COIN": "Crypto", "CRWD": "Tech", "ZS": "Tech",
        "OKTA": "Tech", "DDOG": "Tech",
        "UBER": "Transport", "LYFT": "Transport", "DASH": "Transport", "ZM": "Tech", "ROKU": "Media",
        "SNAP": "Media", "TWILIO": "Tech", "RBLX": "Gaming", "U": "Auto", "PLTR": "Tech"
    }
    
    for ticker in all_candidates:
        df = ticker_data.get(ticker, pd.DataFrame())
        if df.empty or len(df) < 50:  # Need minimum 50 days of data
            continue
        
        price = df["Close"].iloc[-1] if len(df) > 0 else 0
        if price == 0 or price < 0.5:  # Skip penny stocks
            continue
        
        # Calculate metrics
        try:
            rsi = ta_lib.momentum.rsi(df["Close"], window=14).iloc[-1] if len(df) >= 14 else float("nan")
            sma200 = ta_lib.trend.sma_indicator(df["Close"], window=200).iloc[-1] if len(df) >= 200 else float("nan")
            sma50 = ta_lib.trend.sma_indicator(df["Close"], window=50).iloc[-1] if len(df) >= 50 else float("nan")
            fair_val = calculate_fair_value(df, price, ticker)
            ticker_info = fetch_ticker_info(ticker)
        except:
            continue
        
        upside = fair_val["upside"]
        reliability = fair_val["reliability"]
        
        # FILTER 1: Only exclude EXTREME (allow Good, Moderate, High Uncertainty)
        if "EXTREME" in reliability:
            continue
        
        # FILTER 2: Only buy if significant upside (at least 5% - lowered threshold)
        if upside < 5:
            continue
        
        # FILTER 3: Avoid extreme overbought (RSI > 80 - raised threshold)
        if not pd.isna(rsi) and rsi > 80:
            continue
        
        # FILTER 4: P/E ratio check (if available) - avoid very overpriced
        pe_ratio = ticker_info.get("pe_ratio")
        if pe_ratio and pe_ratio > 70:  # Raised from 50 to 70
            continue
        
        # FILTER 5: FUNDAMENTAL HEALTH CHECKS (optional, don't block if data missing)
        # Check earnings growth (positive or at least not super negative)
        earnings_growth = ticker_info.get("earnings_growth")
        if earnings_growth is not None and earnings_growth < -0.75:  # Only skip if earnings down >75%
            continue
        
        # Check debt/equity (avoid extremely over-leveraged)
        debt_to_equity = ticker_info.get("debt_to_equity")
        if debt_to_equity and debt_to_equity > 4.0:  # Raised from 3.0 to 4.0
            continue
        
        # Check profit margin (only skip if deeply unprofitable)
        profit_margin = ticker_info.get("profit_margin")
        if profit_margin is not None and profit_margin < -0.40:  # Raised from -0.25 to -0.40
            continue
        
        # Check ROE (return on equity - quality indicator, but don't be too strict)
        roe = ticker_info.get("roe")
        if roe is not None and roe < -0.05:  # Only skip if negative ROE (very poor)
            continue
        
        # Scoring system - Enterprise version
        score = 0
        reasons = []
        
        # 1. FAIR VALUE is the main signal
        if upside > 30:
            score += 6
            reasons.append("Sterk ondergewaardeerd (>30%)")
        elif upside > 20:
            score += 5
            reasons.append("Zeer ondergewaardeerd (20-30%)")
        elif upside > 15:
            score += 4
            reasons.append("Ondergewaardeerd (15-20%)")
        elif upside > 8:
            score += 2
            reasons.append("Matig ondergewaardeerd (8-15%)")
        
        # 2. RSI Entry Point (critical for timing)
        if not pd.isna(rsi):
            if rsi < 25:
                score += 3
                reasons.append("Extreme oversold - beste entry")
            elif rsi < 30:
                score += 2
                reasons.append("Oversold - goede entry")
            elif rsi < 40:
                score += 1
                reasons.append("Oversold signaal")
        
        # 3. Trend Confirmation
        if not pd.isna(sma200) and not pd.isna(sma50):
            if price > sma200 and sma50 > sma200:
                score += 2
                reasons.append("Strong uptrend (SMA50 > SMA200)")
            elif price > sma200:
                score += 1
                reasons.append("Uptrend confirmed")
        
        # 4. Volume Confirmation
        if len(df) >= 20:
            recent_vol = df["Volume"].tail(5).mean()
            historical_vol = df["Volume"].tail(60).mean()
            if recent_vol > historical_vol * 1.5:
                score += 2
                reasons.append("Volume surge - momentum confirmed")
            elif recent_vol > historical_vol * 1.2:
                score += 1
                reasons.append("Above average volume")
        
        # 5. Momentum (Price > SMA50 > SMA200)
        if not pd.isna(sma50) and not pd.isna(sma200):
            if price > sma50 > sma200:
                score += 1
                reasons.append("Golden cross setup")
        
        # 6. Valuation health (P/E not too high)
        if pe_ratio and 15 < pe_ratio < 30:
            score += 1
            reasons.append("Reasonable P/E ratio")
        
        # 7. FUNDAMENTAL QUALITY BONUSES (optional - if data is available)
        # Earnings growth bonus
        earnings_growth = ticker_info.get("earnings_growth")
        if earnings_growth and earnings_growth > 0.20:  # >20% earnings growth
            score += 2
            reasons.append("Strong earnings growth (>20%)")
        elif earnings_growth and earnings_growth > 0.10:  # >10% earnings growth
            score += 1
            reasons.append("Solid earnings growth (>10%)")
        
        # ROE quality check (higher ROE = better capital efficiency)
        roe = ticker_info.get("roe")
        if roe and roe > 0.20:  # ROE >20% = excellent
            score += 2
            reasons.append("Excellent ROE (>20%)")
        elif roe and roe > 0.12:  # ROE >12% = good
            score += 1
            reasons.append("Good ROE (>12%)")
        
        # Debt health (lower is better)
        debt_to_equity = ticker_info.get("debt_to_equity")
        if debt_to_equity and debt_to_equity < 0.3:  # Very low debt = safer
            score += 1
            reasons.append("Very strong balance sheet")
        elif debt_to_equity and debt_to_equity < 0.7:  # Moderate debt
            score += 0.5
            reasons.append("Solid balance sheet")
        
        # Profit margin (higher = healthier business)
        profit_margin = ticker_info.get("profit_margin")
        if profit_margin and profit_margin > 0.25:  # >25% profit margin
            score += 1
            reasons.append("Excellent profit margins (>25%)")
        elif profit_margin and profit_margin > 0.15:  # >15% profit margin
            score += 0.5
            reasons.append("Good profit margins")
        
        # Only recommend if score >= 3 (lowered from 4)
        if score >= 3 and reasons:
            # Calculate stop loss (support level)
            support = calculate_support_resistance(df).get("support1", price * 0.90)
            stop_loss_pct = ((price - support) / price * 100) if price > 0 else 5
            
            # Check if already owned
            already_owned = ticker in portfolio_tickers
            
            recommendations.append({
                "ticker": ticker,
                "price": price,
                "fair_value": fair_val["fair_value"],
                "upside": upside,
                "rsi": rsi,
                "score": score,
                "reliability": reliability,
                "reasons": " | ".join(reasons),
                "rating": "🟢 STRONG BUY" if score >= 13 else ("🟢 BUY" if score >= 9 else "🟡 BUY"),
                "sector": sector_map.get(ticker, "Other"),
                "pe_ratio": pe_ratio,
                "stop_loss": support,
                "stop_loss_pct": stop_loss_pct,
                "owned": already_owned,  # NEW: Track if user already owns this stock
            })
    
    # FILTER 5: Sector Diversification - max 2 per sector in top results
    sector_counts = {}
    filtered_recommendations = []
    
    for rec in sorted(recommendations, key=lambda x: (x["score"], x["upside"]), reverse=True):
        sector = rec["sector"]
        if sector_counts.get(sector, 0) < 3:  # Allow 3 per sector now
            filtered_recommendations.append(rec)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(filtered_recommendations) >= 20:  # Show up to 20 recommendations
            break
    
    return filtered_recommendations


def calculate_portfolio_metrics(portfolio_positions, ticker_data: dict) -> dict:
    """Calculate portfolio metrics."""
    if not portfolio_positions:
        return {}
    
    total_invested = 0
    total_current = 0
    sectors = {}
    
    for pos in portfolio_positions:
        ticker = pos["ticker"]
        qty = pos["qty"]
        gak = pos["gak"]
        
        cost = qty * gak
        total_invested += cost
        
        df = ticker_data.get(ticker, pd.DataFrame())
        if not df.empty:
            current_price = df["Close"].iloc[-1]
            current_value = qty * current_price
            total_current += current_value
            
            # Get sector
            info = fetch_ticker_info(ticker)
            sector = info.get("sector", "Unknown")
            if sector not in sectors:
                sectors[sector] = 0
            sectors[sector] += current_value
    
    return {
        "total_invested": total_invested,
        "total_current": total_current,
        "total_return": total_current - total_invested,
        "total_return_pct": ((total_current - total_invested) / total_invested * 100) if total_invested > 0 else 0,
        "sectors": sectors,
    }


def build_candlestick(df: pd.DataFrame, ticker: str) -> go.Figure:
    """Candlestick chart with SMA200 overlay + RSI sub-chart."""
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.70, 0.30],
        subplot_titles=(f"{ticker} – Candlestick + SMA200", "RSI (14)"),
    )

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"],
            name="Price",
            increasing_line_color="#3fb950",
            decreasing_line_color="#f85149",
        ),
        row=1, col=1,
    )

    # SMA200
    if "SMA200" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["SMA200"],
                name="SMA 200",
                line=dict(color="#58a6ff", width=1.8, dash="dot"),
            ),
            row=1, col=1,
        )

    # RSI
    if "RSI" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["RSI"],
                name="RSI",
                line=dict(color="#e3b341", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(227,179,65,0.08)",
            ),
            row=2, col=1,
        )
        # Overbought / Oversold reference lines
        for lvl, col in [(70, "#f85149"), (30, "#3fb950")]:
            fig.add_hline(
                y=lvl, line_dash="dash",
                line_color=col, opacity=0.5,
                row=2, col=1,
            )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0e1117",
        plot_bgcolor="#0e1117",
        height=620,
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_yaxes(gridcolor="#2e3140", zerolinecolor="#2e3140")
    fig.update_xaxes(gridcolor="#2e3140", zerolinecolor="#2e3140")
    return fig


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://img.icons8.com/fluency/96/stocks.png",
        width=64,
    )
    st.title("📊 Stock Dashboard")
    st.caption(f"Last refresh: {datetime.now().strftime('%d %b %Y  %H:%M')}")
    st.divider()

    # --- Login / User Selection ---
    st.subheader("👤 Gebruiker")
    
    existing_users = get_all_users()
    if "current_user" not in st.session_state:
        st.session_state.current_user = None
    
    # Option to login or create new user
    user_action = st.radio(
        "Wat wil je doen?",
        ["Inloggen", "Nieuwe gebruiker"],
        label_visibility="collapsed",
        horizontal=True,
    )
    
    if user_action == "Inloggen" and existing_users:
        selected_user = st.selectbox(
            "Selecteer gebruiker",
            options=existing_users,
            label_visibility="collapsed",
        )
        if st.button("🔓 Inloggen", use_container_width=True):
            st.session_state.current_user = selected_user
            st.rerun()
    elif user_action == "Nieuwe gebruiker":
        new_user = st.text_input(
            "Gebruikersnaam",
            placeholder="bijv. papa, broer, jij",
            label_visibility="collapsed",
        ).strip().lower()
        if new_user:
            if new_user in existing_users:
                st.warning("Deze gebruiker bestaat al!")
            elif st.button("✅ Registreer", use_container_width=True):
                # Create new user with default data
                save_user_portfolio(new_user, {"portfolio_raw": "", "watchlist_raw": ""})
                st.session_state.current_user = new_user
                st.success(f"Welkom, {new_user}! 🎉")
                st.rerun()
    
    if st.session_state.current_user:
        st.divider()
        st.info(f"✅ Ingelogd als: **{st.session_state.current_user}**")
        if st.button("🚪 Uitloggen", use_container_width=True):
            st.session_state.current_user = None
            st.rerun()
    else:
        st.warning("⚠️ Gelieve in te loggen of een nieuwe gebruiker aan te maken.")
        st.stop()

    # Load current user's data
    user_data = load_user_portfolio(st.session_state.current_user)
    portfolio_raw = user_data.get("portfolio_raw", "")
    watchlist_raw = user_data.get("watchlist_raw", "")

    # --- Portfolio input - Easy form ---
    st.subheader("🗂️ Portfolio")
    
    col1, col2, col3, col4 = st.columns([1.5, 0.8, 0.8, 0.9])
    with col1:
        new_ticker = st.text_input("Ticker", placeholder="AAPL", label_visibility="collapsed").upper()
    with col2:
        new_qty = st.number_input("Aantal", value=1.0, min_value=0.1, step=0.1, label_visibility="collapsed")
    with col3:
        new_price = st.number_input("Prijs", value=100.0, min_value=0.01, step=0.01, label_visibility="collapsed")
    with col4:
        new_currency = st.selectbox("Munt", options=["USD", "EUR"], label_visibility="collapsed")
    
    if st.button("➕ Stock toevoegen", use_container_width=True):
        if new_ticker:
            # Parse existing portfolio
            existing_lines = [l.strip() for l in portfolio_raw.strip().split('\n') if l.strip()]
            # Add new stock with currency indicator
            existing_lines.append(f"{new_ticker}, {new_qty}, {new_price}, {new_currency}")
            portfolio_raw = '\n'.join(existing_lines)
            # Auto-save
            save_user_portfolio(st.session_state.current_user, {
                "portfolio_raw": portfolio_raw,
                "watchlist_raw": watchlist_raw,
            })
            st.success(f"✅ {new_ticker} ({new_currency}) toegevoegd!")
            st.rerun()
        else:
            st.error("⚠️ Voer een ticker in")
    
    st.caption("Huidige posities (formaat: TICKER, AANTAL, PRIJS, MUNT):")
    if portfolio_raw:
        st.text_area(
            "Posities",
            value=portfolio_raw,
            height=120,
            disabled=True,
            label_visibility="collapsed",
        )
    else:
        st.info("Nog geen stocks in je portfolio")

    # --- Remove stock ---
    if portfolio_raw:
        st.caption("Stock verwijderen:")
        existing_stocks = [l.strip().split(',')[0] for l in portfolio_raw.strip().split('\n') if l.strip()]
        remove_ticker = st.selectbox("Kies stock om te verwijderen", options=existing_stocks, label_visibility="collapsed")
        if st.button("🗑️ Verwijderen", use_container_width=True):
            remaining_lines = [l for l in portfolio_raw.strip().split('\n') if l.strip() and not l.strip().startswith(remove_ticker)]
            portfolio_raw = '\n'.join(remaining_lines)
            save_user_portfolio(st.session_state.current_user, {
                "portfolio_raw": portfolio_raw,
                "watchlist_raw": watchlist_raw,
            })
            st.success(f"✅ {remove_ticker} verwijderd!")
            st.rerun()

    # --- Watchlist input ---
    st.divider()
    st.subheader("👁️ Watchlist")
    watchlist_raw = st.text_input(
        "Tickers (komma-gescheiden)",
        value=watchlist_raw,
        label_visibility="collapsed",
    )
    
    if st.button("💾 Watchlist opslaan", use_container_width=True):
        save_user_portfolio(st.session_state.current_user, {
            "portfolio_raw": portfolio_raw,
            "watchlist_raw": watchlist_raw,
        })
        st.success("✅ Watchlist opgeslagen!")
    
    st.divider()
    if st.button("🔄 Ververs data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ── Parse inputs ──────────────────────────────────────────────────────────────
portfolio_positions = []
eur_to_usd = 1.10  # Exchange rate EUR to USD

for line in portfolio_raw.strip().splitlines():
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= 3:
        try:
            ticker = parts[0].upper()
            qty = float(parts[1])
            price = float(parts[2])
            currency = parts[3].upper() if len(parts) > 3 else "USD"
            
            # Convert EUR to USD if needed
            if currency == "EUR":
                price = price * eur_to_usd
            
            portfolio_positions.append({
                "ticker": ticker,
                "qty": qty,
                "gak": price,  # Always in USD
            })
        except ValueError:
            pass

watchlist_tickers = [t.strip().upper() for t in watchlist_raw.split(",") if t.strip()]

# Popular stocks for recommendations
popular_tickers_list = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "NFLX", "AMD", "INTC",
    "JPM", "BAC", "GS", "WFC", "V", "MA", "AXP", "BLK", "BX",
    "JNJ", "UNH", "PFE", "MRK", "ABBV", "TMO", "LLY", "AZN",
    "WMT", "MCD", "SBUX", "NKE", "HD", "COST", "CVX", "XOM",
    "SOFI", "PYPL", "SQ", "COIN", "CRWD", "ZS", "OKTA", "DDOG", "CRM", "ADBE",
    "VTI", "VOO", "QQQ", "IVV", "SPLG", "SCHX",
    "UBER", "LYFT", "DASH", "ZM", "ROKU", "SNAP", "TWILIO", "RBLX", "U", "PLTR"
]

all_tickers = list({p["ticker"] for p in portfolio_positions} | set(watchlist_tickers))


# ── Fetch all data once ───────────────────────────────────────────────────────
with st.spinner("📡 Data ophalen…"):
    ticker_data: dict[str, pd.DataFrame] = {t: fetch_data(t) for t in all_tickers}
    
    # Also fetch popular stocks for recommendations (in background, don't block)
    st.write("*Loading recommendation data...*")
    for t in popular_tickers_list:
        if t not in ticker_data:
            ticker_data[t] = fetch_data(t)


# ── Helper: latest indicators ─────────────────────────────────────────────────
def latest(ticker: str, col: str) -> float:
    df = ticker_data.get(ticker, pd.DataFrame())
    if df.empty or col not in df.columns:
        return float("nan")
    series = df[col]
    # Flatten in case it's still a DataFrame/multi-column
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    series = series.dropna()
    if series.empty:
        return float("nan")
    return float(series.iloc[-1])


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11 = st.tabs([
    "📁 Portfolio Overzicht", 
    "👁️ Watchlist", 
    "📊 Analytics", 
    "⚙️ Advanced", 
    "🎯 Aanbevelingen",
    "⚠️ Alerts & Taxes",
    "💡 Rebalance & Risk",
    "💰 Income & Compare",
    "🔔 Price Alerts",
    "📈 History & Export",
    "🧮 Position Calculator"
])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 – Portfolio
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.header("Portfolio Overzicht")

    if not portfolio_positions:
        st.info("Voeg posities toe in de zijbalk (TICKER, AANTAL, GAK).")
    else:
        rows = []
        for pos in portfolio_positions:
            t = pos["ticker"]
            price = latest(t, "Close")
            rsi   = latest(t, "RSI")
            sma   = latest(t, "SMA200")
            qty   = pos["qty"]
            gak   = pos["gak"]
            wl    = price * qty if price else 0.0
            cost  = gak * qty
            pnl   = wl - cost
            pnl_p = (pnl / cost * 100) if cost else 0.0
            df = ticker_data.get(t, pd.DataFrame())
            fair_val = calculate_fair_value(df, price, t)
            status = classify_status(price, sma, rsi, df, fair_val["fair_value"])
            
            # Get additional info
            info = fetch_ticker_info(t)
            sector = info.get("sector", "N/A")
            
            rows.append({
                "Ticker": t,
                "Prijs": price,
                "Fair Value": fair_val["fair_value"],
                "GAK": gak,
                "Aantal": qty,
                "Waarde ($)": wl,
                "Kostprijs": cost,
                "W/V ($)": pnl,
                "W/V (%)": pnl_p,
                "RSI": rsi,
                "_status": status,
                "_fair_val_str": fair_val["valuation"],
                "_reliability": fair_val.get("reliability", "✅ Good"),
                "Sector": sector,
            })

        total_value  = sum(r["Waarde ($)"] for r in rows)
        total_cost   = sum(r["Kostprijs"] for r in rows)
        total_pnl    = total_value - total_cost
        total_pnl_p  = (total_pnl / total_cost * 100) if total_cost else 0.0
        
        # Sort by performance (best first)
        rows_sorted = sorted(rows, key=lambda x: x["W/V (%)"], reverse=True)

        # ── Summary Cards ──
        col1, col2, col3, col4 = st.columns(4)
        col1.metric(
            "💼 Totaal Geïnvesteerd",
            f"$ {total_cost:,.2f}",
            label_visibility="visible"
        )
        
        pnl_delta = f"{total_pnl:+,.0f}" if total_pnl != 0 else "0"
        col2.metric(
            "💰 Huidige Waarde",
            f"$ {total_value:,.2f}",
            delta=pnl_delta,
            label_visibility="visible"
        )
        
        pnl_color = "normal" if total_pnl >= 0 else "inverse"
        col3.metric(
            "📈 Totaal W/V",
            f"$ {total_pnl:+,.2f}",
            delta=f"{total_pnl_p:+.2f}%",
            delta_color=pnl_color,
            label_visibility="visible"
        )
        
        # Risk metric
        volatilities = []
        for row in rows:
            df = ticker_data.get(row["Ticker"], pd.DataFrame())
            if not df.empty and len(df) >= 20:
                returns = df["Close"].pct_change()
                vol = returns.std() * 100
                volatilities.append(vol)
        
        avg_vol = sum(volatilities) / len(volatilities) if volatilities else 0
        col4.metric(
            "📊 Portfolio Volatility",
            f"{avg_vol:.2f}%",
            label_visibility="visible"
        )
        
        st.divider()
        
        # ── Sector Breakdown ──
        st.subheader("🏢 Sector Performance")
        sector_stats = {}
        for row in rows_sorted:
            sector = row["Sector"]
            if sector not in sector_stats:
                sector_stats[sector] = {"value": 0, "cost": 0, "pnl": 0}
            sector_stats[sector]["value"] += row["Waarde ($)"]
            sector_stats[sector]["cost"] += row["Kostprijs"]
            sector_stats[sector]["pnl"] += row["W/V ($)"]
        
        sector_cols = st.columns(len(sector_stats))
        for col, (sector, stats) in zip(sector_cols, sector_stats.items()):
            pnl_pct = (stats["pnl"] / stats["cost"] * 100) if stats["cost"] > 0 else 0
            col.metric(
                f"📌 {sector}",
                f"$ {stats['value']:,.0f}",
                delta=f"{pnl_pct:+.1f}%"
            )
        
        st.divider()
        
        # ── Detailed Holdings Table ──
        st.subheader("📋 Gedetailleerde Holdings (gesorteerd op performance)")
        
        # Build colored table
        header_cols = st.columns([0.7, 1, 1, 0.8, 0.8, 1, 1, 1, 0.7, 0.7, 1.2, 1.2])
        headers = ["🎯", "Ticker", "Prijs", "Fair V.", "Aantal", "Waarde", "Kostprijs", "W/V ($)", "W/V %", "RSI", "Status", "Waardering"]
        
        for col, h in zip(header_cols, headers):
            col.markdown(f"**{h}**", help=None)
        
        st.markdown('<hr style="margin:2px 0 4px 0; border-color:#2e3140"/>', unsafe_allow_html=True)
        
        for idx, r in enumerate(rows_sorted, 1):
            # Color coding
            pnl = float(r["W/V ($)"])
            pnl_p = float(r["W/V (%)"])
            pnl_color = "#3fb950" if pnl >= 0 else "#f85149"
            
            rsi = float(r["RSI"]) if not pd.isna(r["RSI"]) else float("nan")
            rsi_color = "#3fb950" if rsi < 35 else ("#f85149" if rsi > 65 else "#e3b341")
            rsi_str = f"{rsi:.1f}" if not pd.isna(rsi) else "N/A"
            
            row_cols = st.columns([0.7, 1, 1, 0.8, 0.8, 1, 1, 1, 0.7, 0.7, 1.2, 1.2])
            
            # Ranking
            if pnl_p >= 20:
                rank = "🥇"
            elif pnl_p >= 5:
                rank = "🥈"
            elif pnl_p >= 0:
                rank = "🥉"
            else:
                rank = "📉"
            row_cols[0].markdown(rank, help=None)
            
            # Ticker
            row_cols[1].markdown(f"**{r['Ticker']}**")
            
            # Price
            row_cols[2].markdown(f"${float(r['Prijs']):.2f}")
            
            # Fair Value
            fv = float(r['Fair Value'])
            row_cols[3].markdown(f"${fv:.2f}")
            
            # Quantity
            row_cols[4].markdown(f"{float(r['Aantal']):.2f}")
            
            # Waarde
            row_cols[5].markdown(f"${float(r['Waarde ($)']):.2f}")
            
            # Kostprijs
            row_cols[6].markdown(f"${float(r['Kostprijs']):.2f}")
            
            # W/V ($)
            row_cols[7].markdown(
                f'<span style="color:{pnl_color}; font-weight:600">${pnl:+,.2f}</span>',
                unsafe_allow_html=True
            )
            
            # W/V %
            row_cols[8].markdown(
                f'<span style="color:{pnl_color}; font-weight:700">{pnl_p:+.1f}%</span>',
                unsafe_allow_html=True
            )
            
            # RSI
            row_cols[9].markdown(
                f'<span style="color:{rsi_color}; font-weight:600">{rsi_str}</span>',
                unsafe_allow_html=True
            )
            
            # Status
            row_cols[10].markdown(status_badge(r["_status"]), unsafe_allow_html=True)
            
            # Waardering
            row_cols[11].markdown(f"<small>{r['_fair_val_str']}</small>", unsafe_allow_html=True)
        
        st.divider()
        
        # ── Portfolio Composition ──
        st.subheader("💡 Portfolio Composition")
        
        comp_cols = st.columns(2)
        
        # Asset allocation by sector (pie)
        with comp_cols[0]:
            sector_values = {}
            for row in rows:
                sector = row["Sector"]
                sector_values[sector] = sector_values.get(sector, 0) + float(row["Waarde ($)"])
            
            if sector_values:
                fig_pie = go.Figure(data=[go.Pie(
                    labels=list(sector_values.keys()),
                    values=list(sector_values.values()),
                    hole=0.3,
                    marker=dict(colors=["#3fb950", "#f85149", "#58a6ff", "#e3b341", "#bc8ef7", "#79c0ff"])
                )])
                fig_pie.update_layout(template="plotly_dark", height=350, title="Sector Allocation")
                st.plotly_chart(fig_pie, use_container_width=True)
        
        # Position sizes (bar)
        with comp_cols[1]:
            position_values = {r["Ticker"]: float(r["Waarde ($)"]) for r in rows_sorted}
            position_pnl = {r["Ticker"]: float(r["W/V (%)"]) for r in rows_sorted}
            
            fig_bar = go.Figure()
            colors = ["#3fb950" if position_pnl[t] >= 0 else "#f85149" for t in position_values.keys()]
            fig_bar.add_trace(go.Bar(
                x=list(position_values.keys()),
                y=list(position_values.values()),
                marker=dict(color=colors),
                text=[f"{position_pnl[t]:.1f}%" for t in position_values.keys()],
                textposition="outside",
            ))
            fig_bar.update_layout(
                template="plotly_dark",
                height=350,
                title="Position Sizes & Returns",
                showlegend=False,
                xaxis_title="",
                yaxis_title="Value ($)"
            )
            st.plotly_chart(fig_bar, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 – Watchlist
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    st.header("Watchlist Analyse")

    if not watchlist_tickers:
        st.info("Voeg tickers toe in de zijbalk (komma-gescheiden).")
    else:
        wl_rows = []
        for t in watchlist_tickers:
            price  = latest(t, "Close")
            rsi    = latest(t, "RSI")
            sma    = latest(t, "SMA200")
            df = ticker_data.get(t, pd.DataFrame())
            fair_val = calculate_fair_value(df, price, t)
            trend  = "🟢 Bullish" if (not pd.isna(sma) and price > sma) else "🔴 Bearish"
            signal = classify_status(price, sma, rsi, df, fair_val["fair_value"])
            wl_rows.append({
                "Ticker": t,
                "Prijs": price,
                "RSI": rsi,
                "SMA200": sma,
                "Trend": trend,
                "Signaal": signal,
            })

        # Render watchlist cards
        for r in wl_rows:
            signal_emoji = "🟢" if r["Signaal"] == "BUY" else ("🔴" if r["Signaal"] == "SELL" else "🟡")
            oversold = r["Signaal"] == "BUY"
            border_color = "#3fb950" if oversold else ("#f85149" if r["Signaal"] == "SELL" else "#2e3140")
            bg_color     = "#0d2318" if oversold else ("#1a0d0d" if r["Signaal"] == "SELL" else "#1c1f26")
            rsi_raw   = float(r["RSI"]) if not pd.isna(r["RSI"]) else float("nan")
            prijs_raw = float(r["Prijs"]) if not pd.isna(r["Prijs"]) else float("nan")
            rsi_val   = f"{rsi_raw:.1f}" if not pd.isna(rsi_raw) else "N/A"
            rsi_color = ("#3fb950" if (not pd.isna(rsi_raw) and rsi_raw < 35) else
                         ("#f85149" if (not pd.isna(rsi_raw) and rsi_raw > 65) else "#e3b341"))
            prijs_str = f"{prijs_raw:,.2f}" if not pd.isna(prijs_raw) else "N/A"

            with st.container():
                st.markdown(
                    f"""
                    <div style="
                        background:{bg_color};
                        border:1.5px solid {border_color};
                        border-radius:12px;
                        padding:14px 20px;
                        margin-bottom:10px;
                    ">
                      <span style="font-size:1.15rem; font-weight:700; color:#e6edf3">{r['Ticker']}</span>
                      &nbsp;&nbsp;
                      <span style="color:#8b949e; font-size:.9rem">$ {prijs_str}</span>
                      &nbsp;&nbsp;|&nbsp;&nbsp;
                      <span style="color:#8b949e; font-size:.9rem">RSI: </span>
                      <span style="font-weight:700; color:{rsi_color}">{rsi_val}</span>
                      &nbsp;&nbsp;|&nbsp;&nbsp;
                      <span style="font-size:.9rem">{r['Trend']}</span>
                      &nbsp;&nbsp;|&nbsp;&nbsp;
                      <span style="font-weight:700; font-size:.95rem">{signal_emoji} {r['Signaal']}</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 – Portfolio Analytics
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    st.header("📊 Portfolio Analytics")
    
    if not portfolio_positions:
        st.info("Voeg stocks toe aan je portfolio om analytics te zien.")
    else:
        # Calculate portfolio metrics
        metrics = calculate_portfolio_metrics(portfolio_positions, ticker_data)
        
        # Display key metrics
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("💰 Totaal Geïnvesteerd", f"$ {metrics.get('total_invested', 0):,.2f}")
        col2.metric("📈 Huidige Waarde", f"$ {metrics.get('total_current', 0):,.2f}")
        col3.metric("💹 Totaal Return", f"$ {metrics.get('total_return', 0):+,.2f}")
        col4.metric("📊 Return %", f"{metrics.get('total_return_pct', 0):+.2f}%")
        
        st.divider()
        
        # Sector allocation
        st.subheader("🏢 Sector Allocation")
        sectors = metrics.get('sectors', {})
        if sectors:
            sector_df = pd.DataFrame({
                'Sector': list(sectors.keys()),
                'Waarde ($)': list(sectors.values())
            })
            sector_df['Percentage'] = (sector_df['Waarde ($)'] / sector_df['Waarde ($)'].sum() * 100).round(1)
            
            col1, col2 = st.columns([2, 1])
            with col1:
                fig = go.Figure(data=[go.Pie(labels=sector_df['Sector'], values=sector_df['Waarde ($)'])])
                fig.update_layout(template="plotly_dark", height=400)
                st.plotly_chart(fig, use_container_width=True)
            
            with col2:
                st.dataframe(sector_df[['Sector', 'Percentage']], use_container_width=True, hide_index=True)
        
        st.divider()
        
        # Risk Analysis
        st.subheader("⚠️ Risk Assessment")
        
        volatilities = []
        for pos in portfolio_positions:
            df = ticker_data.get(pos["ticker"], pd.DataFrame())
            if not df.empty and len(df) >= 20:
                returns = df["Close"].pct_change()
                vol = returns.std() * 100
                volatilities.append(vol)
        
        if volatilities:
            avg_volatility = sum(volatilities) / len(volatilities)
            if avg_volatility < 2:
                risk_level = "🟢 Low Risk (Conservative)"
            elif avg_volatility < 4:
                risk_level = "🟡 Medium Risk (Balanced)"
            else:
                risk_level = "🔴 High Risk (Aggressive)"
            
            col1, col2 = st.columns(2)
            col1.metric("📈 Portfolio Volatility", f"{avg_volatility:.2f}%")
            col2.metric("⚙️ Risk Level", risk_level)


# ════════════════════════════════════════════════════════════════════════════
# TAB 4 – Advanced Analysis
# ════════════════════════════════════════════════════════════════════════════
with tab4:
    st.header("⚙️ Advanced Technical Analysis")
    
    if not all_tickers:
        st.info("Voeg tickers toe om advanced analysis te zien.")
    else:
        selected_ticker_adv = st.selectbox(
            "Selecteer aandeel voor gedetailleerde analyse",
            options=all_tickers,
            key="adv_ticker_select"
        )
        
        if selected_ticker_adv:
            df_adv = ticker_data.get(selected_ticker_adv, pd.DataFrame())
            
            if not df_adv.empty:
                # Get info
                info = fetch_ticker_info(selected_ticker_adv)
                sr = calculate_support_resistance(df_adv)
                current_price = df_adv["Close"].iloc[-1]
                
                # Display info
                st.subheader(f"📋 {selected_ticker_adv} - Gedetailleerde Informatie")
                
                # Technical metrics
                info_cols = st.columns(4)
                info_cols[0].metric("💰 Sektor", info.get("sector", "N/A"))
                info_cols[1].metric("🏭 Industrie", info.get("industry", "N/A"))
                info_cols[2].metric("💵 P/E Ratio", f"{info.get('pe_ratio', 'N/A')}")
                info_cols[3].metric("📊 Dividend Yield", f"{info.get('dividend_yield', 0)*100:.2f}%")
                
                st.divider()
                
                # Fundamental metrics
                st.subheader("📈 Fundamentale Gezondheid")
                
                fund_cols = st.columns(5)
                
                # Earnings growth
                eg = info.get("earnings_growth")
                eg_str = f"{eg*100:+.1f}%" if eg is not None else "N/A"
                eg_color = "🟢" if eg and eg > 0 else ("🔴" if eg and eg < 0 else "⚪")
                fund_cols[0].metric("📊 Earnings Growth", eg_str, delta=eg_color)
                
                # Revenue growth
                rg = info.get("revenue_growth")
                rg_str = f"{rg*100:+.1f}%" if rg is not None else "N/A"
                rg_color = "🟢" if rg and rg > 0 else ("🔴" if rg and rg < 0 else "⚪")
                fund_cols[1].metric("💹 Revenue Growth", rg_str, delta=rg_color)
                
                # ROE
                roe = info.get("roe")
                roe_str = f"{roe*100:.1f}%" if roe is not None else "N/A"
                roe_status = "🟢 Excellent" if roe and roe > 0.15 else ("🟡 Good" if roe and roe > 0.10 else "🔴")
                fund_cols[2].metric("📈 ROE", roe_str, delta=roe_status)
                
                # Debt/Equity
                de = info.get("debt_to_equity")
                de_str = f"{de:.2f}x" if de is not None else "N/A"
                de_status = "🟢 Safe" if de and de < 0.5 else ("🟡 OK" if de and de < 1.5 else "🔴 High")
                fund_cols[3].metric("💳 Debt/Equity", de_str, delta=de_status)
                
                # Profit Margin
                pm = info.get("profit_margin")
                pm_str = f"{pm*100:+.1f}%" if pm is not None else "N/A"
                pm_status = "🟢" if pm and pm > 0.15 else ("🟡" if pm and pm > 0 else "🔴")
                fund_cols[4].metric("💰 Profit Margin", pm_str, delta=pm_status)
                
                st.divider()
                
                # Support & Resistance
                st.subheader("📍 Support & Resistance Levels")
                
                sr_cols = st.columns(5)
                sr_cols[0].metric("🔴 Resistance 2", f"$ {sr.get('resistance2', 0):.2f}")
                sr_cols[1].metric("🟠 Resistance 1", f"$ {sr.get('resistance1', 0):.2f}")
                sr_cols[2].metric("🟡 Pivot Point", f"$ {sr.get('pivot', 0):.2f}")
                sr_cols[3].metric("🟢 Support 1", f"$ {sr.get('support1', 0):.2f}")
                sr_cols[4].metric("🔵 Support 2", f"$ {sr.get('support2', 0):.2f}")
                
                st.divider()
                
                # Volume Analysis
                st.subheader("📊 Volume Analysis")
                vol_trend = calculate_volume_trend(df_adv)
                st.info(f"Volume Trend: {vol_trend}")
                
                # Volume chart
                if len(df_adv) > 0:
                    fig_vol = go.Figure(data=[
                        go.Bar(x=df_adv.index[-30:], y=df_adv["Volume"].tail(30), name="Volume")
                    ])
                    fig_vol.update_layout(template="plotly_dark", height=300, title="30-Day Volume")
                    st.plotly_chart(fig_vol, use_container_width=True)
                
                st.divider()
                
                # Correlation (if portfolio has multiple stocks)
                if len(portfolio_positions) > 1:
                    st.subheader("🔗 Correlation Matrix")
                    
                    # Calculate correlation
                    prices = {}
                    for pos in portfolio_positions[:5]:  # Limit to 5 for performance
                        df_temp = ticker_data.get(pos["ticker"], pd.DataFrame())
                        if not df_temp.empty:
                            prices[pos["ticker"]] = df_temp["Close"]
                    
                    if len(prices) > 1:
                        prices_df = pd.DataFrame(prices)
                        corr = prices_df.corr()
                        
                        fig_corr = go.Figure(data=go.Heatmap(
                            z=corr.values,
                            x=corr.columns,
                            y=corr.columns,
                            colorscale='RdBu'
                        ))
                        fig_corr.update_layout(template="plotly_dark", height=400)
                        st.plotly_chart(fig_corr, use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# TAB 5 – Stock Recommendations
# ════════════════════════════════════════════════════════════════════════════
with tab5:
    st.header("🎯 Stock Aanbevelingen")
    
    # Get market health
    market_health = get_market_health()
    
    # Display market context
    st.subheader("📊 Marktomgeving")
    market_cols = st.columns(3)
    market_cols[0].metric("📈 VIX Index", f"{market_health.get('vix', 'N/A'):.1f}" if not pd.isna(market_health.get('vix')) else "N/A")
    market_cols[1].metric("🎯 Market Regime", market_health.get("market_regime", "N/A"))
    market_cols[2].metric("📊 Bull/Bear", market_health.get("bull_regime", "N/A"))
    
    st.divider()
    
    # Get portfolio tickers
    portfolio_tickers = [p["ticker"] for p in portfolio_positions]
    
    # Get recommendations (works even without watchlist - uses popular stocks)
    recommendations = get_stock_recommendations(watchlist_tickers, portfolio_tickers, ticker_data)
    
    if not recommendations:
        st.info("💡 Geen aanbevelingen op dit moment. De markt wacht op betere koopkansen. Check later opnieuw!")
    else:
        st.subheader(f"🎯 Top {len(recommendations)} Enterprise-Grade Aanbevelingen")
        
        # Summary metrics
        summary_cols = st.columns(4)
        summary_cols[0].metric("📊 Aanbevelingen", len(recommendations))
        summary_cols[1].metric("📈 Gem. Upside", f"{sum([r['upside'] for r in recommendations])/len(recommendations):.1f}%")
        summary_cols[2].metric("⭐ Avg. Score", f"{sum([r['score'] for r in recommendations])/len(recommendations):.1f}/20")
        
        # Count owned stocks
        owned_count = sum(1 for r in recommendations if r.get("owned", False))
        summary_cols[3].metric("✅ Al in Portfolio", f"{owned_count} van {len(recommendations)}")
        
        st.divider()
        
        # Detailed recommendations
        for i, rec in enumerate(recommendations, 1):
            with st.container():
                # Header with rating + owned indicator
                col_header1, col_header2, col_header3 = st.columns([2, 1, 1])
                owned_badge = "✅ IN PORTFOLIO" if rec.get("owned", False) else ""
                col_header1.markdown(f"### {i}. {rec['ticker']} {rec['rating']} {owned_badge}")
                col_header2.metric("Score", f"{rec['score']}/20")
                col_header3.metric("Upside", f"+{rec['upside']:.1f}%")
                
                # Key metrics
                metric_cols = st.columns(6)
                metric_cols[0].metric("💰 Prijs", f"${rec['price']:.2f}")
                metric_cols[1].metric("🎯 Fair Value", f"${rec['fair_value']:.2f}")
                metric_cols[2].metric("📊 RSI", f"{rec['rsi']:.1f}" if not pd.isna(rec['rsi']) else "N/A")
                metric_cols[3].metric("📈 P/E", f"{rec['pe_ratio']:.1f}x" if rec['pe_ratio'] else "N/A")
                metric_cols[4].metric("🛑 Stop Loss", f"${rec['stop_loss']:.2f}")
                metric_cols[5].metric("Risk", f"{rec['stop_loss_pct']:.1f}%")
                
                # Reliability badge
                reliability = rec.get("reliability", "Unknown")
                reliability_map = {
                    "Good Confidence": ("🟢", "Excellent"),
                    "Moderate Confidence": ("🟡", "Good"),
                    "High Uncertainty": ("🔴", "Risky"),
                    "EXTREME": ("⚠️", "Unreliable")
                }
                
                # Find matching reliability
                rel_badge = "❓"
                rel_label = "Unknown"
                for key, (badge, label) in reliability_map.items():
                    if key in reliability:
                        rel_badge = badge
                        rel_label = label
                        break
                
                st.markdown(f"""
                **Sector:** {rec.get('sector', 'Unknown')} | **Betrouwbaarheid:** {rel_badge} {rel_label}
                
                **Redenen:**
                {rec['reasons']}
                """)
                
                # Action buttons
                action_cols = st.columns([1, 1, 1, 2])
                action_cols[0].button(f"📌 Bekijk {rec['ticker']}", key=f"view_{rec['ticker']}")
                action_cols[1].button(f"⭐ Watchlist", key=f"watch_{rec['ticker']}")
                action_cols[2].button(f"💼 Koop", key=f"buy_{rec['ticker']}")
                action_cols[3].markdown("")
                
                st.divider()
        
        # Risk disclaimer
        st.warning("""
        ⚠️ **DISCLAIMER:** 
        - Deze aanbevelingen zijn gebaseerd op technische analyse EN fundamentale data
        - Always do your own research (DYOR) voordat je koopt
        - Stop loss levels zijn suggesties, geen garanties
        - Portefeuillediversificatie is essentieel
        - Past performance is geen garantie voor toekomstige resultaten
        - Market conditions (VIX, sector rotation) kunnen snel veranderen
        """)


# ════════════════════════════════════════════════════════════════════════════
# TAB 6 – Alerts & Tax Loss Harvesting
# ════════════════════════════════════════════════════════════════════════════
with tab6:
    st.header("⚠️ Portfolio Alerts & Tax Benefits")
    
    # Alerts
    st.subheader("🚨 Active Alerts")
    alerts = get_position_alerts(portfolio_positions, ticker_data)
    
    if not alerts:
        st.success("✅ Geen alerts - alles ziet er goed uit!")
    else:
        for alert in alerts:
            if alert["severity"] == "high":
                st.error(f"{alert['type']}: {alert['message']}")
            elif alert["severity"] == "medium":
                st.warning(f"{alert['type']}: {alert['message']}")
            else:
                st.info(f"{alert['type']}: {alert['message']}")
    
    st.divider()
    
    # Tax Loss Harvesting
    st.subheader("💰 Tax Loss Harvesting Opportunities")
    st.caption("Verkoop verliesgevende posities om belastingvoordeel te krijgen")
    
    tax_losses = calculate_tax_loss_harvesting(portfolio_positions, ticker_data)
    
    if not tax_losses:
        st.info("🎉 Geen verliesgevende posities - goed gedaan!")
    else:
        total_tax_benefit = sum(tl["tax_benefit"] for tl in tax_losses)
        st.metric("💵 Potentieel belastingvoordeel", f"$ {total_tax_benefit:,.2f}", help="Bij 24% belastingtarief")
        
        st.divider()
        
        for tl in tax_losses:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric(tl["ticker"], f"${tl['total_loss']:+,.2f}")
            col2.metric("Loss %", f"{tl['loss_pct']:+.1f}%")
            col3.metric("Shares", f"{tl['shares']:.2f}")
            col4.metric("Tax Benefit", f"${tl['tax_benefit']:,.0f}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 7 – Rebalancing & Risk Metrics
# ════════════════════════════════════════════════════════════════════════════
with tab7:
    st.header("💡 Rebalancing & Risk Management")
    
    # Risk Metrics
    st.subheader("📊 Risk Metrics (Professional Grade)")
    
    risk_metrics = calculate_risk_metrics(portfolio_positions, ticker_data)
    
    if risk_metrics:
        metrics_cols = st.columns(5)
        
        var_95 = risk_metrics.get("var_95", 0)
        metrics_cols[0].metric(
            "📉 Value at Risk (95%)",
            f"{var_95:.2%}",
            help="Worst 5% daily loss scenario"
        )
        
        sharpe = risk_metrics.get("sharpe_ratio", 0)
        metrics_cols[1].metric(
            "📈 Sharpe Ratio",
            f"{sharpe:.2f}",
            help="Return vs Risk trade-off (>1 is good)"
        )
        
        max_dd = risk_metrics.get("max_drawdown", 0)
        metrics_cols[2].metric(
            "📉 Max Drawdown",
            f"{max_dd:.2%}",
            help="Worst historical loss"
        )
        
        beta = risk_metrics.get("beta", 0)
        if not pd.isna(beta):
            metrics_cols[3].metric(
                "📊 Beta (vs S&P 500)",
                f"{beta:.2f}",
                help="<1 less volatile, >1 more volatile"
            )
        
        ann_vol = risk_metrics.get("annualized_volatility", 0)
        metrics_cols[4].metric(
            "🎢 Annualized Volatility",
            f"{ann_vol:.2%}",
            help="Expected yearly price swings"
        )
        
        st.divider()
        
        # Interpretation
        st.info(f"""
        **Risk Interpretation:**
        - **Sharpe Ratio**: {sharpe:.2f} {'✅ Good' if sharpe > 1 else '⚠️ Mediocre'} - You're getting {sharpe:.2f} units of return per unit of risk
        - **Max Drawdown**: {max_dd:.1%} - Worst your portfolio could have fallen
        - **Beta**: {beta:.2f} {'✅ Stable' if beta < 1 else '⚠️ Volatile'} - {'Less' if beta < 1 else 'More'} volatile than market
        """)
    else:
        st.warning("Not enough data for risk metrics yet (need 60+ days of history)")
    
    st.divider()
    
    # Rebalancing
    st.subheader("⚖️ Portfolio Rebalancing Suggestions")
    
    rebalance = get_rebalancing_suggestion(portfolio_positions, ticker_data)
    
    if rebalance and rebalance.get("rebalancing"):
        st.caption("Sectors that are over/under-weighted relative to target allocation")
        
        for rec in rebalance["rebalancing"]:
            col1, col2, col3 = st.columns([2, 2, 1])
            col1.metric(
                f"{rec['sector']}",
                f"{rec['current']:.1f}%",
                delta=f"{rec['target']:.1f}% target"
            )
            col2.markdown(f"{rec['action']}")
            col3.metric("Amount", f"$ {rec['amount']:,.0f}")
    else:
        st.success("✅ Portfolio is well-balanced!")


# ════════════════════════════════════════════════════════════════════════════
# TAB 8 – Income & Benchmark Comparison
# ════════════════════════════════════════════════════════════════════════════
with tab8:
    st.header("💰 Income & Performance")
    
    # Dividend Income
    st.subheader("💵 Dividend Income")
    
    dividend_info = get_dividend_info(portfolio_positions, ticker_data)
    
    if dividend_info.get("dividends"):
        st.metric(
            "📊 Annual Dividend Income",
            f"$ {dividend_info['total_annual_income']:,.2f}",
            help=f"From {dividend_info['count']} dividend-paying stocks"
        )
        
        st.divider()
        
        # Dividend breakdown
        for div in dividend_info["dividends"]:
            col1, col2, col3 = st.columns(3)
            col1.metric(div["ticker"], f"{div['yield']:.2f}%")
            col2.metric("Annual", f"$ {div['annual_dividend']:,.2f}")
            col3.metric("Position Value", f"$ {div['position_value']:,.2f}")
    else:
        st.info("No dividend-paying stocks in your portfolio yet")
    
    st.divider()
    
    # Benchmark Comparison
    st.subheader("🏆 Performance vs S&P 500")
    
    benchmark = compare_vs_benchmark(portfolio_positions, ticker_data)
    
    if benchmark:
        col1, col2, col3 = st.columns(3)
        
        port_ret = benchmark.get("portfolio_return", 0)
        sp500_ret = benchmark.get("sp500_return", 0)
        outperf = benchmark.get("outperformance", 0)
        
        col1.metric(
            "📈 Your Return (1Y)",
            f"{port_ret:+.2f}%",
            delta_color="normal"
        )
        
        col2.metric(
            "📊 S&P 500 Return",
            f"{sp500_ret:+.2f}%" if not pd.isna(sp500_ret) else "N/A",
            label_visibility="visible"
        )
        
        if not pd.isna(outperf):
            color = "normal" if outperf >= 0 else "inverse"
            col3.metric(
                "🎯 Outperformance",
                f"{outperf:+.2f}%",
                delta_color=color
            )
        
        # Interpretation
        if not pd.isna(outperf):
            if outperf > 5:
                st.success(f"🎉 Beating the market by {outperf:.1f}%! Great job!")
            elif outperf > 0:
                st.info(f"✅ Ahead of S&P 500 by {outperf:.1f}%")
            elif outperf > -5:
                st.warning(f"⚠️ Underperforming by {abs(outperf):.1f}% - consider rebalancing")
            else:
                st.error(f"❌ Significantly underperforming ({outperf:.1f}%) - review strategy")


# ════════════════════════════════════════════════════════════════════════════
# TAB 9 – Price Alerts
# ════════════════════════════════════════════════════════════════════════════
with tab9:
    st.header("🔔 Price Alerts")
    st.caption("Ontvang meldingen wanneer stocks een bepaalde prijs bereiken")
    
    # Load existing alerts
    price_alerts = user_data.get("price_alerts", [])
    
    # Check triggered alerts
    triggered = check_price_alerts(price_alerts, ticker_data)
    
    if triggered:
        st.subheader("🚨 Triggered Alerts!")
        for alert in triggered:
            st.success(f"{alert['type']}: {alert['message']}")
        st.divider()
    
    # Add new alert
    st.subheader("➕ Nieuwe Alert Toevoegen")
    
    alert_cols = st.columns([2, 1, 1, 1])
    with alert_cols[0]:
        alert_ticker = st.selectbox(
            "Stock",
            options=all_tickers if all_tickers else ["AAPL"],
            key="alert_ticker"
        )
    with alert_cols[1]:
        current_price_alert = latest(alert_ticker, "Close") if alert_ticker else 0
        st.metric("Huidige Prijs", f"${current_price_alert:.2f}")
    with alert_cols[2]:
        alert_target = st.number_input(
            "Target Prijs",
            value=float(current_price_alert * 1.1),
            min_value=0.01,
            step=0.01,
            key="alert_target"
        )
    with alert_cols[3]:
        alert_type = st.selectbox(
            "Alert Type",
            options=["above", "below"],
            format_func=lambda x: "📈 Boven" if x == "above" else "📉 Onder",
            key="alert_type"
        )
    
    if st.button("🔔 Alert Toevoegen", use_container_width=True):
        new_alert = {
            "ticker": alert_ticker,
            "target_price": alert_target,
            "type": alert_type,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        price_alerts.append(new_alert)
        user_data["price_alerts"] = price_alerts
        save_user_portfolio(st.session_state.current_user, user_data)
        st.success(f"✅ Alert toegevoegd voor {alert_ticker} @ ${alert_target:.2f}")
        st.rerun()
    
    st.divider()
    
    # Show existing alerts
    st.subheader("📋 Actieve Alerts")
    
    if not price_alerts:
        st.info("Nog geen alerts ingesteld")
    else:
        for i, alert in enumerate(price_alerts):
            col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
            col1.write(f"**{alert['ticker']}**")
            col2.write(f"Target: ${alert['target_price']:.2f}")
            col3.write(f"{'📈 Boven' if alert['type'] == 'above' else '📉 Onder'}")
            if col4.button("🗑️", key=f"del_alert_{i}"):
                price_alerts.pop(i)
                user_data["price_alerts"] = price_alerts
                save_user_portfolio(st.session_state.current_user, user_data)
                st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# TAB 10 – Portfolio History & Export
# ════════════════════════════════════════════════════════════════════════════
with tab10:
    st.header("📈 Portfolio History & Export")
    
    # Record today's snapshot
    portfolio_history = user_data.get("portfolio_history", [])
    portfolio_history = record_portfolio_snapshot(portfolio_positions, ticker_data, portfolio_history)
    user_data["portfolio_history"] = portfolio_history
    save_user_portfolio(st.session_state.current_user, user_data)
    
    # History Chart
    st.subheader("📊 Portfolio Waarde Over Tijd")
    
    if len(portfolio_history) > 1:
        history_df = pd.DataFrame(portfolio_history)
        history_df["date"] = pd.to_datetime(history_df["date"])
        
        fig_history = go.Figure()
        fig_history.add_trace(go.Scatter(
            x=history_df["date"],
            y=history_df["value"],
            name="Portfolio Waarde",
            fill="tozeroy",
            line=dict(color="#3fb950", width=2),
            fillcolor="rgba(63,185,80,0.2)"
        ))
        fig_history.add_trace(go.Scatter(
            x=history_df["date"],
            y=history_df["cost"],
            name="Geïnvesteerd",
            line=dict(color="#58a6ff", width=2, dash="dash")
        ))
        
        fig_history.update_layout(
            template="plotly_dark",
            height=400,
            title="Portfolio Performance",
            xaxis_title="Datum",
            yaxis_title="Waarde ($)",
            legend=dict(orientation="h", yanchor="bottom", y=1.02)
        )
        st.plotly_chart(fig_history, use_container_width=True)
        
        # Performance metrics
        if len(portfolio_history) >= 2:
            first_val = portfolio_history[0]["value"]
            last_val = portfolio_history[-1]["value"]
            total_return = ((last_val - first_val) / first_val * 100) if first_val > 0 else 0
            
            metric_cols = st.columns(4)
            metric_cols[0].metric("Start Waarde", f"${first_val:,.2f}")
            metric_cols[1].metric("Huidige Waarde", f"${last_val:,.2f}")
            metric_cols[2].metric("Totaal Rendement", f"{total_return:+.2f}%")
            metric_cols[3].metric("Dagen Gevolgd", f"{len(portfolio_history)}")
    else:
        st.info("📊 Portfolio history wordt vanaf vandaag bijgehouden. Kom morgen terug voor de grafiek!")
    
    st.divider()
    
    # Export Section
    st.subheader("📥 Export Portfolio")
    
    export_cols = st.columns(2)
    
    with export_cols[0]:
        st.markdown("**CSV Export**")
        st.caption("Download je portfolio als spreadsheet")
        
        if portfolio_positions:
            csv_data = export_portfolio_csv(portfolio_positions, ticker_data)
            st.download_button(
                label="📄 Download CSV",
                data=csv_data,
                file_name=f"portfolio_{st.session_state.current_user}_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
                use_container_width=True
            )
        else:
            st.info("Voeg stocks toe om te exporteren")
    
    with export_cols[1]:
        st.markdown("**Portfolio Summary**")
        st.caption("Kopieer-klare samenvatting")
        
        if portfolio_positions:
            summary_lines = [f"Portfolio {st.session_state.current_user} - {datetime.now().strftime('%Y-%m-%d')}"]
            summary_lines.append("-" * 40)
            
            total_val = 0
            total_cost = 0
            for pos in portfolio_positions:
                df = ticker_data.get(pos["ticker"], pd.DataFrame())
                if not df.empty:
                    price = df["Close"].iloc[-1]
                    val = pos["qty"] * price
                    cost = pos["qty"] * pos["gak"]
                    pnl = val - cost
                    total_val += val
                    total_cost += cost
                    summary_lines.append(f"{pos['ticker']}: {pos['qty']} shares @ ${price:.2f} = ${val:.2f} ({pnl:+.2f})")
            
            summary_lines.append("-" * 40)
            summary_lines.append(f"Total: ${total_val:,.2f} (P/L: ${total_val-total_cost:+,.2f})")
            
            st.text_area("Summary", value="\n".join(summary_lines), height=200)
    
    st.divider()
    
    # News Section
    st.subheader("📰 Recent News")
    
    news_ticker = st.selectbox(
        "Selecteer stock voor nieuws",
        options=all_tickers if all_tickers else ["AAPL"],
        key="news_ticker"
    )
    
    if news_ticker:
        news = fetch_stock_news(news_ticker)
        
        if news:
            for article in news:
                with st.container():
                    st.markdown(f"""
                    **{article['title']}**  
                    📰 {article['publisher']} | 📅 {article['published']}  
                    [Lees meer]({article['link']})
                    """)
                    st.divider()
        else:
            st.info(f"Geen recent nieuws gevonden voor {news_ticker}")
    
    st.divider()
    
    # Earnings Calendar
    st.subheader("📅 Earnings Calendar")
    
    earnings_data = []
    for pos in portfolio_positions[:10]:  # Limit for performance
        earnings = get_earnings_calendar(pos["ticker"])
        if earnings.get("earnings_date"):
            earnings_data.append({
                "Ticker": pos["ticker"],
                "Earnings Date": earnings["earnings_date"],
                "Days Until": earnings["days_until"]
            })
    
    if earnings_data:
        earnings_df = pd.DataFrame(earnings_data)
        earnings_df = earnings_df.sort_values("Days Until", key=lambda x: pd.to_numeric(x, errors='coerce'))
        
        for _, row in earnings_df.iterrows():
            days = row["Days Until"]
            if isinstance(days, (int, float)) and days <= 7:
                st.warning(f"⚠️ **{row['Ticker']}** earnings in {days} dagen! ({row['Earnings Date']})")
            elif isinstance(days, (int, float)) and days <= 30:
                st.info(f"📅 **{row['Ticker']}** earnings op {row['Earnings Date']} ({days} dagen)")
            else:
                st.write(f"📆 **{row['Ticker']}** earnings: {row['Earnings Date']}")
    else:
        st.info("Geen earnings data beschikbaar voor je portfolio stocks")


# ════════════════════════════════════════════════════════════════════════════
# TAB 11 – Position Size Calculator
# ════════════════════════════════════════════════════════════════════════════
with tab11:
    st.header("🧮 Position Size Calculator")
    st.caption("Bereken de juiste positiegrootte gebaseerd op risicomanagement")
    
    # Portfolio value
    total_portfolio_value = sum(
        pos["qty"] * ticker_data.get(pos["ticker"], pd.DataFrame())["Close"].iloc[-1]
        for pos in portfolio_positions
        if not ticker_data.get(pos["ticker"], pd.DataFrame()).empty
    )
    
    st.subheader("💰 Account Settings")
    
    calc_cols = st.columns(2)
    with calc_cols[0]:
        account_value = st.number_input(
            "Portfolio Waarde ($)",
            value=float(total_portfolio_value) if total_portfolio_value > 0 else 10000.0,
            min_value=100.0,
            step=100.0,
            help="Je totale beschikbare kapitaal"
        )
    with calc_cols[1]:
        risk_per_trade = st.slider(
            "Risico per Trade (%)",
            min_value=0.5,
            max_value=5.0,
            value=2.0,
            step=0.5,
            help="Hoeveel % van je portfolio wil je riskeren per trade? (1-2% aanbevolen)"
        )
    
    st.divider()
    
    st.subheader("📊 Trade Details")
    
    trade_cols = st.columns(3)
    with trade_cols[0]:
        calc_ticker = st.selectbox(
            "Stock",
            options=all_tickers + ["Custom"],
            key="calc_ticker"
        )
    
    if calc_ticker != "Custom":
        current_calc_price = latest(calc_ticker, "Close") if calc_ticker else 100
        sr = calculate_support_resistance(ticker_data.get(calc_ticker, pd.DataFrame()))
        suggested_stop = sr.get("support1", current_calc_price * 0.95) if sr else current_calc_price * 0.95
    else:
        current_calc_price = 100.0
        suggested_stop = 95.0
    
    with trade_cols[1]:
        entry_price = st.number_input(
            "Entry Prijs ($)",
            value=float(current_calc_price),
            min_value=0.01,
            step=0.01
        )
    with trade_cols[2]:
        stop_loss = st.number_input(
            "Stop Loss ($)",
            value=float(suggested_stop),
            min_value=0.01,
            step=0.01,
            help="Support level of maximum verlies"
        )
    
    # Calculate position size
    if st.button("🧮 Bereken Positiegrootte", use_container_width=True):
        result = calculate_position_size(account_value, risk_per_trade, entry_price, stop_loss)
        
        if result:
            st.divider()
            st.subheader("📊 Resultaat")
            
            # Risk-Based Method
            st.markdown("### 🎯 Risk-Based Position Size")
            
            res_cols = st.columns(4)
            res_cols[0].metric("Aantal Shares", f"{result['shares']}")
            res_cols[1].metric("Positie Waarde", f"${result['position_value']:,.2f}")
            res_cols[2].metric("% van Portfolio", f"{result['position_pct']:.1f}%")
            res_cols[3].metric("Risico ($)", f"${result['risk_amount']:,.2f}")
            
            st.info(f"""
            **Berekening:**
            - Risico bedrag: ${result['risk_amount']:,.2f} ({risk_per_trade}% van ${account_value:,.0f})
            - Risico per share: ${result['risk_per_share']:.2f} (Entry ${entry_price:.2f} - Stop ${stop_loss:.2f})
            - Aantal shares: ${result['risk_amount']:,.2f} / ${result['risk_per_share']:.2f} = **{result['shares']} shares**
            """)
            
            # Kelly Criterion
            st.markdown("### 📈 Kelly Criterion (Agressiever)")
            st.caption("Gebaseerd op 50% win rate en 2:1 reward/risk ratio")
            
            kelly_cols = st.columns(3)
            kelly_cols[0].metric("Kelly Shares", f"{result['kelly_shares']}")
            kelly_cols[1].metric("Kelly Waarde", f"${result['kelly_value']:,.2f}")
            kelly_cols[2].metric("Kelly %", f"{(result['kelly_value']/account_value*100):.1f}%")
            
            st.warning("""
            ⚠️ **Disclaimer:**
            - Risk-based sizing is conservatiever en veiliger
            - Kelly Criterion kan leiden tot grote posities
            - Gebruik maximaal 50% van Kelly ("Half Kelly") voor minder risico
            - Always DYOR en pas je risico aan op je comfort level
            """)
        else:
            st.error("Kon positiegrootte niet berekenen. Controleer je input.")


# ════════════════════════════════════════════════════════════════════════════
# INTERACTIVE CHART (below both tabs)
# ════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("📊 Interactieve Koersgrafiek (Enhanced)")

chart_col, ctrl_col = st.columns([4, 1])
with ctrl_col:
    selected_ticker = st.selectbox(
        "Kies aandeel",
        options=all_tickers if all_tickers else ["–"],
        index=0,
    )
    period_choice = st.selectbox(
        "Periode",
        options=["3mo", "6mo", "1y", "2y", "5y"],
        index=2,
        format_func=lambda x: {
            "3mo": "3 maanden", "6mo": "6 maanden",
            "1y": "1 jaar", "2y": "2 jaar", "5y": "5 jaar"
        }[x],
    )
    
    st.divider()
    st.caption("📈 Indicatoren")
    show_bb = st.checkbox("Bollinger Bands", value=True)
    show_macd = st.checkbox("MACD", value=True)

with chart_col:
    if selected_ticker and selected_ticker != "–":
        chart_df = fetch_data(selected_ticker, period=period_choice)
        if not chart_df.empty:
            fig = build_enhanced_chart(chart_df, selected_ticker, show_bb=show_bb, show_macd=show_macd)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning(f"Geen data beschikbaar voor **{selected_ticker}**.")

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style="text-align:center; color:#484f58; font-size:.78rem; margin-top:32px">
      Stock Dashboard · Data via Yahoo Finance · Refresh elke 5 min
    </div>
    """,
    unsafe_allow_html=True,
)
