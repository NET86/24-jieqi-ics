# 中国二十四节气日历

纯净的 24 节气 ICS 订阅，不包含传统节日或法定假日。

最近核验：2026-09-18 · 范围：2015–2050 · 数据源：[香港天文台](https://www.hko.gov.hk/tc/gts/time/conversion.htm)

## 订阅

```text
https://raw.githubusercontent.com/NET86/24-jieqi-ics/master/24_solar_terms_2015-01-01_2050-12-31.ics
```

[直接下载 ICS](https://github.com/NET86/24-jieqi-ics/raw/refs/heads/master/24_solar_terms_2015-01-01_2050-12-31.ics)

## 自动维护

- 每月 1 日、15 日 01:01（Asia/Shanghai）自动核验。
- 2015–2050 年表需通过香港天文台多个官方入口一致性、24 节气顺序/日期窗口/间隔及 UID 校验；当前年和未来两年再与香港天文台天文 XML 交叉核对。
- 官方少量修订会自动更新并保留原 UID。单个节气日期变化超过 ±1 天，或一次出现 24 个及以上日期变化，本次核验失败，不发布任何改动。
- 任一核验失败都保留上一版；成功后才更新“最近核验”日期。
- 范围暂止于 2050。香港天文台提示 2051 年春分存在远期跨日不确定性，因此不自动向后预测。

## 来源与许可

最初基于 [KaitoHH/24-jieqi-ics](https://github.com/KaitoHH/24-jieqi-ics)，数据生成思路来自 [infinet/lunar-calendar](https://github.com/infinet/lunar-calendar)，现由 NET86 独立维护。BSD 许可见 [COPYRIGHT](COPYRIGHT)；商业使用前请自行确认香港天文台相关数据授权。
