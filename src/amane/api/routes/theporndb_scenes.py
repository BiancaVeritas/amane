import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from httpx2 import AsyncClient, Timeout
from pydantic import BaseModel, Field

from ...enums import SiteName
from ..deps import RuntimeDep

router = APIRouter(prefix="/theporndb-scenes", tags=["theporndb-scenes"])


class SceneSearchRequest(BaseModel):
    query: str = Field(default="", max_length=200)
    date: str = Field(default="", max_length=40)


@router.post("/search")
async def search_scenes(req: SceneSearchRequest, runtime: RuntimeDep) -> dict[str, list[dict[str, Any]]]:
    query = req.query.strip()
    date = req.date.strip()
    if not query and not date:
        raise HTTPException(422, detail="标题、片商或日期至少填写一项")

    cfg = runtime.config.hot.scraping.site_config.get(str(SiteName.THEPORNDB))
    token = cfg.api_token if cfg else None
    if not token:
        raise HTTPException(503, detail="未配置 ThePornDB API Token")

    # 使用 ThePornDB 官方 REST 场景搜索接口，而不是 GraphQL searchScene。
    # 单输入框分别映射到官方 title / site 参数并行检索，再按 UUID 合并；日期
    # 作为独立服务端条件同时传给两路请求，不在本地对标题搜索结果做日期过滤。
    normalized_date = date.replace(".", "-").replace("/", "-") if date else ""
    searches: list[dict[str, str | int]] = []
    if query:
        searches.extend(
            (
                {"title": query, "per_page": 50, "page": 1, "orderBy": "most_relevant"},
                {"site": query, "per_page": 50, "page": 1, "orderBy": "most_relevant"},
            )
        )
    else:
        searches.append({"per_page": 50, "page": 1, "orderBy": "recently_released"})
    if normalized_date:
        for params in searches:
            params["date"] = normalized_date
    try:
        async with AsyncClient(
            proxy=runtime.config.hot.network.proxy,
            timeout=Timeout(30),
            follow_redirects=True,
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.get(
                        "https://api.theporndb.net/scenes",
                        params=params,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    for params in searches
                )
            )
        payloads = []
        for response in responses:
            response.raise_for_status()
            payloads.append(response.json())
    except Exception as exc:
        raise HTTPException(502, detail=f"ThePornDB 搜索请求失败：{exc}") from exc

    scenes_by_id: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise HTTPException(502, detail="ThePornDB 返回了无效响应")
        for scene in payload["data"]:
            if isinstance(scene, dict) and scene.get("id"):
                scenes_by_id.setdefault(str(scene["id"]), scene)
    result: list[dict[str, Any]] = []
    for scene in scenes_by_id.values():
        site = scene.get("site")
        # REST SceneResource 的封面字段与 GraphQL images { url } 不同。
        # 优先使用 poster，其次 image；兼容 background.large 及旧 images 列表。
        studio = scene.get("studio")
        images = scene.get("images")
        background = scene.get("background")
        image: Any = scene.get("poster") or scene.get("image")
        if isinstance(image, dict):
            image = image.get("url") or image.get("large")
        if not image and isinstance(background, dict):
            image = background.get("large") or background.get("url")
        if not image and isinstance(images, list):
            for candidate in images:
                if isinstance(candidate, str) and candidate.strip():
                    image = candidate
                    break
                if isinstance(candidate, dict):
                    image = candidate.get("url") or candidate.get("large") or candidate.get("image")
                    if isinstance(image, dict):
                        image = image.get("url") or image.get("large")
                    if image:
                        break
        if not isinstance(image, str) or not image.strip():
            image = None
        result.append(
            {
                "id": str(scene["id"]),
                "title": scene.get("title") or "",
                "date": scene.get("date") or "",
                "duration": scene.get("duration"),
                "studio": (
                    site.get("name", "")
                    if isinstance(site, dict)
                    else studio.get("name", "")
                    if isinstance(studio, dict)
                    else site
                    if isinstance(site, str)
                    else ""
                ),
                "image": image,
            }
        )
    return {"items": result}
