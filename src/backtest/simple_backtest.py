from src.features.basic_features import load_data, add_features

df = add_features(load_data())

# dumb strategy: buy when spread tight
df["signal"] = df["spread_pct"] < 0.01

print(df[["timestamp", "mid", "spread_pct", "signal"]].tail())