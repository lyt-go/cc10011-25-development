"""业务模块路由汇总。

这里统一按别名导入再暴露 ROUTERS：模块名有可能和内置名撞车（某个业务模块就叫 dict、list
这种名字时），按名字直接 import 会把内置类型覆盖掉，函数注解在运行时求值就会报
'module' object is not subscriptable。
"""
from __future__ import annotations

from app.routers import realtimemetric as router_realtimemetric
from app.routers import dashboard as router_dashboard
from app.routers import drilldown as router_drilldown
from app.routers import threshold as router_threshold
from app.routers import anomalydetect as router_anomalydetect
from app.routers import dataservice as router_dataservice
from app.routers import alarmhistory as router_alarmhistory
from app.routers import comparison as router_comparison
from app.routers import aggregate as router_aggregate
from app.routers import filterpreset as router_filterpreset
from app.routers import userinsight as router_userinsight
from app.routers import eventanalysis as router_eventanalysis
from app.routers import segmentprofile as router_segmentprofile
from app.routers import userevent as router_userevent
from app.routers import trendforecast as router_trendforecast
from app.routers import viztemplate as router_viztemplate
from app.routers import subscription as router_subscription
from app.routers import dimensionmap as router_dimensionmap
from app.routers import qualityscore as router_qualityscore

ROUTERS = [router_realtimemetric, router_dashboard, router_drilldown, router_threshold, router_anomalydetect, router_dataservice, router_alarmhistory, router_comparison, router_aggregate, router_filterpreset, router_userinsight, router_eventanalysis, router_segmentprofile, router_trendforecast, router_viztemplate, router_subscription, router_dimensionmap, router_qualityscore, router_userevent]
