"""提交时推断内容类型: 手动搜索带了来源站点时按站点所属路由定类型."""

from __future__ import annotations

import pytest

from amane.api.models.tasks import ScrapeSubmission
from amane.parsing import ContentType


def _payload(**kwargs: object):
    body = {"type": "scrape", "use_cache": [], **kwargs}
    return ScrapeSubmission.model_validate(body).build_payload(None)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # 糖心没有番号体系, 标题按番号规则会落到兜底的 WESTERN; 站点本身说明它是国产.
        (
            {
                "number": "温温兔-淫荡的家庭护士 体验40度的肉棒 骑乘榨精精液灌穴",
                "manual": True,
                "preferred_site": "tangxin",
                "search_sites": ["tangxin"],
            },
            ContentType.CHINESE,
        ),
        # 只给 search_sites 同样生效.
        (
            {"number": "某个中文标题", "search_sites": ["tangxin"]},
            ContentType.CHINESE,
        ),
        # 没带来源站点时仍按番号推断.
        ({"number": "MIDV-123"}, ContentType.CENSORED),
        ({"number": "n0888"}, ContentType.UNCENSORED),
        # 显式给了类型就不覆盖.
        ({"number": "某个中文标题", "content_type": "fc2", "preferred_site": "tangxin"}, ContentType.FC2),
    ],
)
def test_infer_type_from_site(kwargs: dict[str, object], expected: ContentType) -> None:
    assert _payload(**kwargs).content_type is expected
