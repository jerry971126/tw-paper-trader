"""四個虛擬帳戶的進出場規則。

所有規則只看「訊號日當天收盤後」已知的資料；實際成交一律在下一個交易日開盤（見 engine.py）。
每個策略提供兩個函式：
    exits(acct, panel, day)   -> [(代號, 原因)]            要賣出的持股
    entries(acct, panel, day) -> [(代號, 分數, 原因)]       想買進的候選，由好到壞排序
"""
import config as C


class Strategy:
    key = ""
    name = ""
    desc = ""
    max_positions = C.MAX_POSITIONS
    needs_inst = False

    def exits(self, acct, p, day):
        return []

    def entries(self, acct, p, day):
        return []


class Trend(Strategy):
    key = "A"
    name = "A 趨勢動能"
    desc = (f"進場：站上 20 日與 60 日均線，且 20 日漲幅在標的池前 {C.TREND_TOPN} 名。"
            f"出場：跌破 20 日均線，或自成交價虧損 {C.TREND_STOP:.0%} 停損。")

    def exits(self, acct, p, day):
        out = []
        for code, pos in acct["positions"].items():
            c = p.val("close", day, code)
            ma20 = p.val("ma20", day, code)
            if c <= pos["entry_price"] * (1 - C.TREND_STOP):
                out.append((code, f"停損 -{C.TREND_STOP:.0%}"))
            elif c < ma20:
                out.append((code, "跌破20日均線"))
        return out

    def entries(self, acct, p, day):
        u = p.universe
        c, ma20, ma60, r = (p.close.loc[day, u], p.ma20.loc[day, u],
                            p.ma60.loc[day, u], p.ret20.loc[day, u])
        ok = (c > ma20) & (c > ma60) & (r > 0)
        ranked = r[ok].sort_values(ascending=False).head(C.TREND_TOPN)
        return [(code, float(v), f"20日漲幅 {v:.1%}，站上60日均線") for code, v in ranked.items()]


class MeanReversion(Strategy):
    key = "B"
    name = "B 均值回歸"
    desc = (f"進場：股價在 200 日均線之上（長多）且 RSI(14) 低於 {C.MR_RSI_ENTRY}。"
            f"出場：RSI 回到 {C.MR_RSI_EXIT} 以上，或持有滿 {C.MR_MAX_HOLD} 個交易日。")

    def exits(self, acct, p, day):
        out = []
        for code, pos in acct["positions"].items():
            rsi = p.val("rsi14", day, code)
            if rsi >= C.MR_RSI_EXIT:
                out.append((code, f"RSI 回升至 {rsi:.0f}"))
            elif pos["days_held"] >= C.MR_MAX_HOLD:
                out.append((code, f"持有滿 {C.MR_MAX_HOLD} 個交易日"))
        return out

    def entries(self, acct, p, day):
        u = p.universe
        c, ma200, rsi = p.close.loc[day, u], p.ma200.loc[day, u], p.rsi14.loc[day, u]
        ok = (c > ma200) & (rsi < C.MR_RSI_ENTRY)
        ranked = rsi[ok].sort_values().head(10)
        return [(code, float(v), f"RSI {v:.0f} 超賣，仍在200日均線之上") for code, v in ranked.items()]


class InstFlow(Strategy):
    key = "C"
    name = "C 法人籌碼"
    needs_inst = True
    desc = (f"進場：外資連續 {C.FLOW_BUY_DAYS} 日買超，且股價在 20 日均線之上"
            f"（依 3 日買超量佔均量比例排序）。"
            f"出場：外資連續 {C.FLOW_SELL_DAYS} 日賣超，或跌破 20 日均線。")

    def exits(self, acct, p, day):
        out = []
        for code in acct["positions"]:
            if bool(p.sell_streak.at[day, code]):
                out.append((code, f"外資連續 {C.FLOW_SELL_DAYS} 日賣超"))
            elif p.val("close", day, code) < p.val("ma20", day, code):
                out.append((code, "跌破20日均線"))
        return out

    def entries(self, acct, p, day):
        u = p.universe
        ok = p.buy_streak.loc[day, u] & (p.close.loc[day, u] > p.ma20.loc[day, u])
        flow = p.flow3.loc[day, u]
        ranked = flow[ok & flow.notna()].sort_values(ascending=False).head(10)
        return [(code, float(v), f"外資連 {C.FLOW_BUY_DAYS} 日買超，3日買超佔均量 {v:.1%}")
                for code, v in ranked.items()]


class BuyHold(Strategy):
    key = "D"
    name = "D 買進持有 0050"
    max_positions = 1
    desc = "對照組：開跑第一天全額買進 0050，之後不動。"

    def entries(self, acct, p, day):
        if acct["positions"] or acct["fills"]:
            return []
        if p.benchmark not in p.close.columns:
            return []
        return [(p.benchmark, 1.0, "基準：全額買進並持有")]


ALL_STRATEGIES = [Trend(), MeanReversion(), InstFlow(), BuyHold()]
