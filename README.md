# CT V4.0 — Professional AI Spot Trading Platform

نظام تداول مؤسسي كامل مبني على Clean Architecture بـ 14 محرك مستقل.

## 🏗️ Architecture

```
core/           # Foundation: events, base classes, types, errors
engines/        # 14 independent engines
services/       # Orchestration layer
strategies/     # Isolated trading strategies
database/       # Models + Repositories
bots/telegram/  # Telegram UI
config/         # Environment-based configuration
```

## 🚀 Quick Start

1. Copy `.env.example` to `.env` and fill in your values
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `python main.py`

## 🧩 Engines

| # | Engine | Responsibility |
|---|--------|---------------|
| 1 | Config Engine | Environment variables, validation |
| 2 | Market Data Engine | WebSocket, live prices, candles |
| 3 | Market Analyzer | Trend, momentum, volatility, structure |
| 4 | Strategy Engine | Runs all strategies in isolation |
| 5 | Evidence Engine | Aggregates signals → BUY/SELL/HOLD/IGNORE |
| 6 | Risk Engine | Position size, drawdown, capital protection |
| 7 | Execution Engine | Order execution (simulation mode) |
| 8 | Portfolio Engine | Virtual portfolio tracking |
| 9 | Learning Engine | Performance evaluation, recommendations |
| 10 | Reporting Engine | Telegram reports |
| 11 | Health Monitor | System monitoring, auto-recovery |
| 12 | Logging Engine | Centralized logging |
| 13 | Telegram Engine | Bot UI |
| 14 | Database Engine | Persistence layer |

## 📊 Data Flow

```
Market Data → Analyzer → Strategies → Evidence → Risk → Execution → Database → Reporting → Telegram
```

## 🔒 Design Rules

- No circular dependencies
- Event-driven communication between engines
- Only Execution Engine can place orders
- No strategy accesses Portfolio directly
- Risk Engine has absolute veto power
- Trades are immutable (append-only)
