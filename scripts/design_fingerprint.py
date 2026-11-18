"""用例设计内容指纹：门禁报告绑定与执行台账共用同一实现。

单独成模块的原因：`gate_checks` 产出 binding.design_hash，`execution_ledger` 校验它，
两处若各写一份哈希逻辑，排序/分隔符/编码任一处漂移都会让"内容绑定"静默失效。
"""

import hashlib
import json


def fingerprint(value):
    """对 JSON 可序列化对象取稳定指纹；拒绝 NaN/Infinity，避免不同平台的序列化差异。"""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
