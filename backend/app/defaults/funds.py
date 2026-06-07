from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DefaultFund:
    code: str
    name: str
    category: str
    tracking_index_code: str | None
    target_allocation: float


DEFAULT_RESEARCH_FUNDS: tuple[DefaultFund, ...] = (
    DefaultFund("110020", "易方达沪深300ETF联接A", "broad_index", "CSI300", 0.0333),
    DefaultFund("000051", "华夏沪深300ETF联接A", "broad_index", "CSI300", 0.0333),
    DefaultFund("007339", "易方达沪深300ETF联接C", "broad_index", "CSI300", 0.0333),
    DefaultFund("001052", "华夏中证500ETF联接A", "broad_index", "CSI500", 0.0333),
    DefaultFund("162711", "广发中证500ETF联接A", "broad_index", "CSI500", 0.0333),
    DefaultFund("000478", "建信中证500指数增强A", "broad_index", "CSI500", 0.0333),
    DefaultFund("110026", "易方达创业板ETF联接A", "broad_index", "CHINEXT", 0.0333),
    DefaultFund("011608", "易方达上证科创50联接A", "broad_index", None, 0.0333),
    DefaultFund("001051", "华夏上证50ETF联接A", "broad_index", None, 0.0333),
    DefaultFund("090010", "大成中证红利指数A", "broad_index", None, 0.0333),
    DefaultFund("161725", "招商中证白酒指数(LOF)A", "sector", None, 0.0333),
    DefaultFund("160222", "国泰国证食品饮料行业(LOF)A", "sector", None, 0.0333),
    DefaultFund("000248", "汇添富中证主要消费ETF联接A", "sector", None, 0.0333),
    DefaultFund("001180", "广发医药卫生联接A", "sector", None, 0.0333),
    DefaultFund("161726", "招商国证生物医药指数(LOF)A", "sector", None, 0.0333),
    DefaultFund("001594", "天弘中证银行ETF联接A", "sector", None, 0.0333),
    DefaultFund("001552", "天弘中证证券保险A", "sector", None, 0.0333),
    DefaultFund("020021", "国泰金融ETF联接A", "sector", None, 0.0333),
    DefaultFund("005693", "广发中证军工ETF联接C", "sector", None, 0.0333),
    DefaultFund("001618", "天弘中证电子ETF联接C", "sector", None, 0.0333),
    DefaultFund("001629", "天弘中证计算机ETF联接A", "sector", None, 0.0333),
    DefaultFund("004752", "广发中证传媒ETF联接A", "sector", None, 0.0333),
    DefaultFund("110007", "易方达稳健收益债券A", "bond", None, 0.0333),
    DefaultFund("003547", "鹏华丰禄债券", "bond", None, 0.0333),
    DefaultFund("070009", "嘉实超短债债券C", "bond", None, 0.0333),
    DefaultFund("000217", "华安黄金ETF联接C", "commodity", None, 0.0333),
    DefaultFund("270042", "广发纳斯达克100ETF联接人民币(QDII)A", "qdii", "NDX100", 0.0333),
    DefaultFund("050025", "博时标普500ETF联接A", "qdii", "SP500", 0.0333),
    DefaultFund("000071", "华夏恒生ETF联接A", "qdii", None, 0.0333),
    DefaultFund("006327", "易方达中证海外互联网50ETF联接(QDII)A", "qdii", None, 0.0333),
)


DEFAULT_RESEARCH_FUND_CODES = tuple(fund.code for fund in DEFAULT_RESEARCH_FUNDS)
