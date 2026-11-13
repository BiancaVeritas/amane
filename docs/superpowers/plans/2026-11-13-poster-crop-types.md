# 自动裁切的类型开关 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户按内容类型开关自动裁切海报，取代硬编码的有码 / 无码 / FC2 放行集。

**Architecture:** 放行集从常量改为 `ScrapingConfig.poster_crop_types` 配置项；`allows_poster_crop` 收该集合做成员判定，刮削与整理两个入口共用。「裁切海报」仍是全局门。前端在设置页「裁切海报」开关右侧挂一个按钮，弹窗内按 `content_routes` 的键动态渲染开关列表。

**Tech Stack:** Python 3.14 / Pydantic / pytest / React + Mantine / TanStack Form / i18next

**Spec:** 本文件即设计记录（会话内已确认）；决策来源见末尾「已确认决策」。

## Global Constraints

- 放行判定只按**条目类型**（`ContentType`），不按来源站点。`_prepare_image_content_type` 返回的图片排布口径只决定无码裁切比例，**不得**用于放行判定。
- 新增配置项必须 `json_schema_extra={"x-hidden": True}`，不进入主表单（与 `poster_crop_rules` 一致），由专用弹窗读写。
- 弹窗的类型列表**从 `content_routes` 的键生成**，不写死枚举；新增类型路由时列表自动出现。
- 默认值保持 `[CENSORED, UNCENSORED, FC2]`，升级后行为不变。
- 文档只写当前状态与仍生效的约束，禁止迁移动机（`docs/dev/writing.md`）。
- 提交说明、注释用项目开发中文（`docs/dev/terms.md`）。

## Review Focus

- **配置里出现未知类型字符串**：例如旧配置或手改配置写了已删除的类型。期望：构造成员判定失败而不是让整个刮削任务崩。
- **`poster_crop_types` 为空列表**：用户把所有类型都关掉。期望：不裁切，且不报错、不退回默认值。
- **放行集含某类型但 `crop_poster` 关闭**：期望：一律不裁，两者是「与」关系。
- **`content_routes` 缺少某个类型键**（用户删过）：期望：弹窗不渲染该类型，保存时也不把它写没——只写放行集，不触碰 `content_routes`。
- **整理入口的条目类型来自 `Metadata.content_type`**：期望：与刮削期同一放行集，不因路径改写而漂移。

---

### Task 1: 配置项与放行判定

**Files:**
- Modify: `src/amane/config/manager.py`（`ScrapingConfig` 内，`crop_poster` 定义附近）
- Modify: `src/amane/media/images.py`（`allows_poster_crop` 与 `_AUTO_CROP_CONTENT_TYPES`）
- Modify: `src/amane/media/pipeline.py`（`materialize_images` 的放行判定）
- Modify: `src/amane/handlers/file.py`（`_place_library_images` 的放行判定）
- Test: `tests/media/test_images.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces:
  - `ScrapingConfig.poster_crop_types: list[ContentType]`，默认 `[ContentType.CENSORED, ContentType.UNCENSORED, ContentType.FC2]`
  - `allows_poster_crop(content_type: ContentType | None, allowed: Collection[ContentType]) -> bool`
  - `materialize_images(..., content_type, image_content_type, ...)` 的放行改为 `allows_poster_crop(content_type, config.scraping.poster_crop_types)`

- [ ] **Step 1: 写失败测试**

在 `tests/media/test_images.py` 末尾追加：

```python
class TestAllowsPosterCrop:
    @pytest.mark.parametrize(
        ("content_type", "allowed", "expected", "desc"),
        [
            (ContentType.CENSORED, [ContentType.CENSORED], True, "在放行集内"),
            (ContentType.CHINESE, [ContentType.CENSORED], False, "不在放行集内"),
            (ContentType.CHINESE, [], False, "放行集为空一律不裁"),
            (None, [ContentType.CENSORED], False, "类型未知不裁"),
        ],
    )
    def test_decision(self, content_type, allowed, expected, desc):
        assert allows_poster_crop(content_type, allowed) is expected, desc
```

顶部导入补 `allows_poster_crop` 与 `ContentType`（该文件已有 `ContentType` 导入则复用）。

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/media/test_images.py::TestAllowsPosterCrop -v`
Expected: FAIL，报 `allows_poster_crop() takes 1 positional argument but 2 were given`

- [ ] **Step 3: 改判定函数**

`src/amane/media/images.py`：删掉 `_AUTO_CROP_CONTENT_TYPES` 常量，函数改为：

```python
def allows_poster_crop(content_type: ContentType | None, allowed: Collection[ContentType]) -> bool:
    """自动裁切只对放行集内的类型启用; 类型未知不裁."""
    return content_type is not None and content_type in allowed
```

模块顶部补 `from collections.abc import Collection, Mapping`（`Mapping` 原已导入，合并即可）。

- [ ] **Step 4: 加配置项**

`src/amane/config/manager.py` 的 `ScrapingConfig` 中，`crop_poster` 之后加：

```python
poster_crop_types: list[ContentType] = Field(
    default_factory=lambda: [ContentType.CENSORED, ContentType.UNCENSORED, ContentType.FC2],
    json_schema_extra={"x-hidden": True},
)
"""自动裁切放行的内容类型; 与 crop_poster 是「与」关系. 列表为空则一律不裁."""
```

确认 `ContentType` 已从 `..parsing` 或等价位置导入（该文件已用 `ContentType`）。

- [ ] **Step 5: 两个入口接上配置**

`src/amane/media/pipeline.py` 的判定行改为：

```python
if config.scraping.crop_poster and thumb_local and allows_poster_crop(content_type, config.scraping.poster_crop_types):
```

`src/amane/handlers/file.py` 的 `_place_library_images` 判定改为：

```python
and allows_poster_crop(content_type, config.scraping.poster_crop_types)
```

两处都不改比例逻辑：无码比例仍按 `image_content_type or content_type`。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/media tests/handlers -q`
Expected: PASS（含上一轮新增的单图无码用例）

- [ ] **Step 7: 提交**

```bash
git add src/amane/config/manager.py src/amane/media/images.py src/amane/media/pipeline.py src/amane/handlers/file.py tests/media/test_images.py
git commit -m "feat(media): 自动裁切的放行类型改为配置项"
```

---

### Task 2: 配置项在接口 schema 中可见

**Files:**
- Modify: `web/openapi.json`（生成物）
- Modify: `web/src/client/**`（生成物）
- Test: 无（生成物由 `just generate` 保证一致）

**Interfaces:**
- Consumes: Task 1 的 `ScrapingConfig.poster_crop_types`
- Produces: 前端可通过 `useConfig()` 读到 `scraping.poster_crop_types: string[]`

- [ ] **Step 1: 重新生成 OpenAPI 与前端 client**

Run: `just generate`
Expected: 退出码 0；`git status` 显示 `web/openapi.json` 与 `web/src/client/*` 有改动

- [ ] **Step 2: 确认字段出现在 schema 里**

Run: `uv run pytest tests/api/test_schema_repo_compat.py -q`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add web/openapi.json web/src/client
git commit -m "chore(api): 导出裁切类型开关的配置项"
```

---

### Task 3: 设置页的开关按钮

**Files:**
- Create: `web/src/components/settings/crop-types-dialog.tsx`
- Modify: `web/src/routes/settings.tsx`
- Modify: `web/src/i18n/locales/zh-CN/settings.json`
- Modify: `web/src/i18n/locales/en/settings.json`
- Modify: `web/src/lib/z-index.ts`

**Interfaces:**
- Consumes: `useConfig()` 的 `scraping.poster_crop_types` 与 `scraping.content_routes`
- Produces:
  - `export function CropTypesDialog(props: { opened: boolean; onClose: () => void; selected: string[]; options: string[]; onChange: (next: string[]) => void })`
  - 按钮行为：写入 `{ scraping: { poster_crop_types: next } }`

- [ ] **Step 1: 加层叠高度**

`web/src/lib/z-index.ts` 的 `ruleList` 之后加：

```ts
  /** 自动裁切的类型开关. */
  cropTypes: 320,
```

- [ ] **Step 2: 写弹窗组件**

`web/src/components/settings/crop-types-dialog.tsx`：

```tsx
import { Group, Modal, Stack, Switch, Text } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { Z_INDEX } from "@/lib/z-index";

export interface CropTypesDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 已放行的类型; 与后端 ``scraping.poster_crop_types`` 一致. */
  selected: string[];
  /** 可选类型, 由类型路由的键生成. */
  options: string[];
  onChange: (next: string[]) => void;
}

export function CropTypesDialog({
  opened,
  onClose,
  selected,
  options,
  onChange,
}: CropTypesDialogProps) {
  const { t } = useTranslation("settings");
  const chosen = new Set(selected);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t("cropTypes.dialogTitle")}
      size="min(94vw, 32rem)"
      centered
      zIndex={Z_INDEX.cropTypes}
    >
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          {t("cropTypes.dialogHint")}
        </Text>
        {options.map((type) => (
          <Group key={type} justify="space-between" wrap="nowrap" gap="md">
            <Text size="sm">
              {/* 类型名复用类型路由的选项文案, 不再单独维护一份. */}
              {t(`fields.content_routes.options.${type}` as never, { defaultValue: type })}
            </Text>
            <Switch
              checked={chosen.has(type)}
              onChange={(e) => {
                const next = new Set(chosen);
                if (e.currentTarget.checked) next.add(type);
                else next.delete(type);
                onChange([...next]);
              }}
              aria-label={type}
            />
          </Group>
        ))}
      </Stack>
    </Modal>
  );
}
```

- [ ] **Step 3: 挂到设置页**

`web/src/routes/settings.tsx`：

1. 导入 `IconPhoto`（自 `@tabler/icons-react`，与现有 `IconCrop` 同处）与 `CropTypesDialog`。
2. 加状态 `const [cropTypesOpen, setCropTypesOpen] = useState(false);`
3. 加两个派生值：

```tsx
const cropTypes = useMemo<string[]>(() => {
  const raw = (values as { scraping?: { poster_crop_types?: string[] } } | undefined)?.scraping
    ?.poster_crop_types;
  return Array.isArray(raw) ? raw : [];
}, [values]);

const cropTypeOptions = useMemo<string[]>(() => {
  const routes = (values as { scraping?: { content_routes?: Record<string, unknown> } } | undefined)
    ?.scraping?.content_routes;
  return routes ? Object.keys(routes) : [];
}, [values]);
```

4. 把 `afterField` 里挂在 `poster_crop_detect_face` 后面的内容改成「精准裁切」与「裁切类型」两个按钮并排：原来那个 `Group` 内的 `Box` 保持不动，把现有单个 `Button` 换成：

```tsx
<Group gap="xs" wrap="nowrap">
  <Button
    size="xs"
    variant="light"
    leftSection={<IconCrop size={14} />}
    onClick={() => setCropRulesOpen(true)}
  >
    {t("cropRules.button")}
  </Button>
  <Button
    size="xs"
    variant="light"
    leftSection={<IconPhoto size={14} />}
    onClick={() => setCropTypesOpen(true)}
  >
    {t("cropTypes.button")}
  </Button>
</Group>
```

5. 在 `CropRulesDialog` 之后加：

```tsx
<CropTypesDialog
  opened={cropTypesOpen}
  onClose={() => setCropTypesOpen(false)}
  selected={cropTypes}
  options={cropTypeOptions}
  onChange={(next) =>
    saveMutation.mutate({ body: { scraping: { poster_crop_types: next } } })
  }
/>
```

- [ ] **Step 4: 加文案**

`web/src/i18n/locales/zh-CN/settings.json` 顶层加：

```json
"cropTypes": {
  "button": "裁切类型",
  "dialogTitle": "自动裁切的类型",
  "dialogHint": "开启的类型才会从封面自动裁切海报。按条目类型判定, 与来源站点无关。"
}
```

`web/src/i18n/locales/en/settings.json` 对应加英文：`"Crop types"` / `"Types with automatic cropping"` / `"Only enabled types crop posters from the cover. Decided by entry type, not by source site."`

- [ ] **Step 5: 类型检查与 lint**

Run: `just check-web`
Expected: PASS（oxlint / oxfmt / tsc / i18n 全绿）

- [ ] **Step 6: 提交**

```bash
git add web/src/components/settings/crop-types-dialog.tsx web/src/routes/settings.tsx web/src/lib/z-index.ts web/src/i18n/locales/zh-CN/settings.json web/src/i18n/locales/en/settings.json
git commit -m "feat(web): 设置页增加自动裁切的类型开关"
```

---

### Task 4: 文档

**Files:**
- Modify: `docs/dev/task-system.md`（「刮削期资源物化」小节）
- Modify: `docs/user/scraping.md`（「图片下载与海报裁切」小节）

**Interfaces:**
- Consumes: Task 1 的配置项与 Task 3 的开关
- Produces: 无

- [ ] **Step 1: 改开发文档**

`docs/dev/task-system.md` 里替换 Task 1 之前那一行（现文为「裁剪海报 → 有码 / 无码 / FC2 从 thumb 裁切 …」），压成：

```
- 裁剪海报 → 放行集由 `scraping.poster_crop_types` 给出 (与 `crop_poster` 是「与」关系), 从 thumb 裁切; 超分就地覆盖. 失败不阻断刮削. 放行按**条目类型**判定, 与图片排布口径无关: 无码单图若按排布口径会被改写为 `WESTERN`, 两者混用会让全部单图无码停止裁切.
```

- [ ] **Step 2: 改用户文档**

`docs/user/scraping.md` 的「图片下载与海报裁切」小节，把「只有有码、无码与 FC2 生效」那条改为：

```
- 只有「裁切类型」里开启的类型生效, 默认有码、无码与 FC2; 其余类型的封面不保证是横版夹页, 裁切会切掉人像.
```

`docs/user/scraping.md` 的设置项表里，「裁切海报」一行之后加：

```
| 裁切类型 | 有码、无码、FC2 | 只有这些类型从封面自动裁切海报 |
```

- [ ] **Step 3: 提交**

```bash
git add docs/dev/task-system.md docs/user/scraping.md
git commit -m "docs: 自动裁切改为按类型开关"
```

---

### Task 5: 端到端验证

**Files:** 无（只验证）

**Interfaces:**
- Consumes: Task 1-4 的全部产出
- Produces: 无

- [ ] **Step 1: Python 侧相关测试**

Run: `uv run pytest tests/media tests/handlers tests/api -q`
Expected: PASS

- [ ] **Step 2: 类型检查无新增诊断**

Run: `uv run ty check 2>&1 | Select-String "Found \d+ diagnostics"`
Expected: `Found 89 diagnostics`（改动前的基线，不得增加）

- [ ] **Step 3: 重启测试平台并确认进程晚于改动**

Run（分别后台启动）：后端 `AMANE_PORT=8001 uv run uvicorn amane.api.app:create_app --factory --host 127.0.0.1 --port 8001`；前端 `web/` 下 `AMANE_PORT=8001 pnpm exec vite --port 5174 --host 127.0.0.1`
Expected: 两者均返回 200；进程启动时间晚于源码 mtime

- [ ] **Step 4: 手工确认界面**

打开 `http://127.0.0.1:5174/settings?section=scraping`，确认「裁切类型」按钮在「精准裁切」右侧、尺寸与配色一致；弹窗列出全部类型路由的键，默认勾选有码 / 无码 / FC2；拨动任一开关后配置落库。

---

## 已确认决策

- 总开关「裁切海报」保留，管全局；关闭时一律不裁（含类型开启）。
- 默认值维持有码 / 无码 / FC2，升级后行为不变。
- 维度按内容类型而非来源站点：站点只决定问哪些站，裁切只看条目类型；同一站挂在多个类型路由下时各按自己的类型判定。
