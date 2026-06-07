from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultEtf:
    code: str
    name: str
    exchange: str
    theme_tags: tuple[str, ...]
    trading_rule_label: str
    asset_class: str


DEFAULT_SHORT_ETFS: tuple[DefaultEtf, ...] = (
    DefaultEtf("159915", "创业板ETF", "SZ", ("科技", "成长", "宽基"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("588000", "科创50ETF", "SH", ("科技", "科创", "半导体"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("512480", "半导体ETF", "SH", ("半导体", "芯片", "科技"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("512760", "芯片ETF", "SH", ("芯片", "半导体", "科技"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("515050", "5GETF", "SH", ("通信", "5G", "科技"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("159819", "人工智能ETF", "SZ", ("AI", "计算机", "科技"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("516160", "新能源ETF", "SH", ("新能源", "电力设备"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("515790", "光伏ETF", "SH", ("光伏", "电力设备", "新能源"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("515030", "新能源汽车ETF", "SH", ("新能源车", "电力设备"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("512660", "军工ETF", "SH", ("军工", "高端制造"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("510300", "沪深300ETF", "SH", ("宽基", "核心资产"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("510500", "中证500ETF", "SH", ("宽基", "中盘"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("512100", "中证1000ETF", "SH", ("宽基", "小盘"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("512800", "银行ETF", "SH", ("金融", "低估值"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("512000", "券商ETF", "SH", ("金融", "高弹性"), "T+1股票ETF", "equity_etf"),
    DefaultEtf("518880", "黄金ETF", "SH", ("黄金", "避险"), "T+0可能ETF", "commodity_etf"),
    DefaultEtf("511010", "国债ETF", "SH", ("债券", "低波动"), "T+0可能ETF", "bond_etf"),
    DefaultEtf("513100", "纳指ETF", "SH", ("跨境", "纳斯达克", "科技"), "T+0可能ETF", "cross_border_etf"),
)


DEFAULT_SHORT_ETF_CODES = tuple(etf.code for etf in DEFAULT_SHORT_ETFS)
