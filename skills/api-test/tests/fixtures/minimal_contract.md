# 创建 AI 主页

## 接口地址
https://example.test/openapi/default.aspx

## 请求参数
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| action | int | 是 | 动作标识 1000107 |
| title | string | 是 | 主页标题，最长 50 字 |
| cover | string | 否 | 封面图地址 |

## 响应参数
| 参数 | 类型 | 说明 |
|---|---|---|
| result | bool | 是否成功 |
| id | string | 创建成功后的主页 ID |

## 错误码
| 错误码 | 说明 |
|---|---|
| 1001 | 参数缺失 |
| 1002 | 标题超长 |