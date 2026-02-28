import pandas as pd
import sqlite3


def load_data():
    conn = sqlite3.connect("data/kalshi.db")
    df = pd.read_sql("SELECT * FROM orderbook", conn)
    conn.close()
    return df


def add_features(df):
    df["return"] = df["mid"].pct_change()
    df["spread_pct"] = df["spread"] / df["mid"]
    df["volatility"] = df["return"].rolling(20).std()
    return df


if __name__ == "__main__":
    df = add_features(load_data())
    print(df.tail())