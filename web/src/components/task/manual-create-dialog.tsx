import {
  ActionIcon,
  Autocomplete,
  Button,
  FileButton,
  Group,
  Image,
  Menu,
  Modal,
  MultiSelect,
  Pill,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCrop, IconPhotoUp, IconX } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ClearableTextarea, ClearableTextInput } from "@/components/common/clearable-input";
import {
  type CropBox,
  type CropRotation,
  ImageCropDialog,
} from "@/components/media/image-crop-dialog";
import {
  createMetadataMutation,
  getSourcePresetsOptions,
  uploadPosterMutation,
  uploadThumbMutation,
} from "@/client/@tanstack/react-query.gen";
import type { ContentType, MetadataPartial } from "@/client/types.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { CONTENT_TYPES } from "@/lib/exhaustive-maps";
import {
  type CandidateOption,
  filterCandidates,
  resolveEnterChoice,
  selectedLabel,
  useMappingCandidates,
} from "@/lib/media/mapping-candidates";
import { Z_INDEX } from "@/lib/z-index";

/** 与服务端 ``MAX_UPLOAD_BYTES`` 一致; 前端先挡一次, 避免白传大文件. */
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const ACCEPTED = ["image/jpeg", "image/png", "image/webp"] as const;
/** 海报宽高比 (w/h), 与后端默认 `scraping.poster_ratio` 一致; 手动裁切按它初始选。 */
const POSTER_ASPECT = 0.7;

interface ManualCreateDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 预填番号: 从任务进来时带上那条任务的番号, 用户多半是想手工补这一条. */
  initialNumber?: string | null;
}

type Draft = {
  title: string;
  original_title: string;
  plot: string;
  release: string;
  runtime: string;
  studio: string;
  publisher: string;
  series: string;
  /** 演员与标签改成结构化多选: 值来自库里已有的分类, 手输的新名字原样保留. */
  actors: string[];
  directors: string[];
  tags: string[];
};

const EMPTY: Draft = {
  title: "",
  original_title: "",
  plot: "",
  release: "",
  runtime: "",
  studio: "",
  publisher: "",
  series: "",
  actors: [],
  directors: [],
  tags: [],
};

/**
 * 分类多选的数据源: **库里存的原始名**, 显示时附上译名作提示.
 *
 * 不能直接用 `GET /facets/tag`: 那边返回的是映射后的译文 (无修正), 而条目里存的是原始名
 * (無修正 / 完全無修正). 用译文填进去等于把不存在的标签写进库, 所以改走
 * `GET /mappings/{group}/candidates`, 它成对给出 ``{original, label}``.
 */

/**
 * 可创建的分类多选.
 *
 * Mantine 9 的 `MultiSelect` **只能从 `data` 里选**: 内部 keydown 只处理空格与退格,
 * 回车不会把搜索框里的新词加成一项. 这里自己算「回车该选谁」, 抢在组件之前处理 ——
 * 只改表单, 落库发生在点「创建」时.
 */
function CreatableMultiSelect({
  label,
  placeholder,
  options,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  options: CandidateOption[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [search, setSearch] = useState("");

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    // 中文输入法的组合输入还没结束时, 回车是在确认候选字, 不能当提交.
    if (event.nativeEvent.isComposing) return;
    const choice = resolveEnterChoice(search, options);
    if (choice === null) return;
    // 抢在组件自己的键盘处理之前: 它见到 defaultPrevented 就会直接返回.
    event.preventDefault();
    event.stopPropagation();
    if (!value.includes(choice.value)) onChange([...value, choice.value]);
    setSearch("");
  };

  return (
    <MultiSelect
      label={label}
      // 断开标签联动: label 的 htmlFor 会让点标题那一整段都把焦点送进输入框.
      labelProps={{ htmlFor: undefined }}
      placeholder={placeholder}
      data={options}
      value={value}
      onChange={onChange}
      searchValue={search}
      onSearchChange={setSearch}
      onKeyDown={handleKeyDown}
      searchable
      clearable
      // 关掉「聚焦即开」: 点标签会让输入框获焦, 那时不该弹候选.
      // 点击控件本身由组件自己的 onClick 展开, 不要再接管 dropdownOpened ——
      // 受控后它的 openDropdown 会变成空操作, 下拉就再也打不开了.
      openOnFocus={false}
      comboboxProps={{ zIndex: Z_INDEX.dropdown }}
      // 下拉里显示 `原始名 (译名)` 才选得准; 选中后值已定, pill 只显示译名.
      renderPill={({ option, value: itemValue, onRemove, reorderProps }) => (
        // reorderProps 必须展开: 拖动排序靠它挂的拖拽事件, 漏了这条 pill 就拖不动.
        <Pill withRemoveButton onRemove={onRemove} size="sm" {...reorderProps}>
          {selectedLabel(options, String(itemValue ?? option?.value ?? ""))}
        </Pill>
      )}
    />
  );
}

/**
 * 单值片商输入: 可从片商表里挑, 也可手输.
 *
 * 制作商 / 发行商 / 系列都是唯一值, 不需要多选也不需要拖动; 候选只为省打字.
 * 选中后取的是**原始名** —— 与标签、演员同一口径.
 */
function VendorInput({
  label,
  placeholder,
  options,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  options: CandidateOption[];
  value: string;
  onChange: (next: string) => void;
}) {
  const [search, setSearch] = useState("");

  return (
    <Autocomplete
      label={label}
      // 断开标签联动: label 的 htmlFor 会让点标题那一整段都把焦点送进输入框.
      labelProps={{ htmlFor: undefined }}
      placeholder={placeholder}
      // 候选写 `原始名 (译名)`; 输入框本身显示译名 (selectedLabel).
      data={filterCandidates(options, search).map((option) => option.label)}
      value={search || selectedLabel(options, value)}
      onChange={(next) => {
        setSearch(next);
        // 手输时直接按原文写回; 点选由 onOptionSubmit 覆盖成原始名.
        onChange(next);
      }}
      onOptionSubmit={(picked) => {
        const hit = options.find((option) => option.label === picked);
        if (hit) {
          setSearch("");
          onChange(hit.value);
        }
      }}
      // 失焦时清掉搜索词, 让输入框回到「显示当前值」的状态.
      onBlur={() => setSearch("")}
      // 关掉「聚焦即开」: 点标签会让输入框获焦, 那时不该弹候选.
      // 点击输入框本身由组件自己的 onClick 展开; 不要再接管 dropdownOpened ——
      // 受控后它的 openDropdown 会变成空操作, 下拉就再也打不开了.
      openOnFocus={false}
      comboboxProps={{ zIndex: Z_INDEX.dropdown }}
    />
  );
}

/**
 * 按自然像素框把本地图片裁出来, 返回新的 File.
 *
 * 本地裁切不经过服务端: 用户在点「创建」之前就能看到裁好的封面, 不满意可以重裁或换图.
 * 旋转按 `rotation` 顺时针处理, 与后端 `CropBoxRequest.rotation` 同一约定.
 */
async function cropFileToBlob(file: File, box: CropBox, rotation: CropRotation): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  const swap = rotation === 90 || rotation === 270;
  // 旋转后的画布尺寸: 90/270 度时宽高互换.
  const canvas = document.createElement("canvas");
  canvas.width = swap ? bitmap.height : bitmap.width;
  canvas.height = swap ? bitmap.width : bitmap.height;

  const ctx = canvas.getContext("2d");
  if (ctx === null) throw new Error("无法创建画布上下文");
  ctx.translate(canvas.width / 2, canvas.height / 2);
  ctx.rotate((rotation * Math.PI) / 180);
  ctx.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);
  bitmap.close();

  const w = Math.max(1, Math.round(box.right - box.left));
  const h = Math.max(1, Math.round(box.bottom - box.top));
  const out = document.createElement("canvas");
  out.width = w;
  out.height = h;
  const outCtx = out.getContext("2d");
  if (outCtx === null) throw new Error("无法创建画布上下文");
  outCtx.drawImage(canvas, Math.round(box.left), Math.round(box.top), w, h, 0, 0, w, h);

  return new Promise<Blob>((resolve, reject) => {
    out.toBlob(
      (blob) => (blob === null ? reject(new Error("导出图片失败")) : resolve(blob)),
      "image/png",
    );
  });
}

/**
 * 换图: 建新 blob 地址并释放旧地址, 同时写回 File.
 *
 * 提在组件外是为了避免每次渲染重建; 撤销严格发生在替换时, 不会误伤正在使用的地址.
 */
function setPreview(
  next: File | null,
  setFile: (file: File | null) => void,
  setUrl: React.Dispatch<React.SetStateAction<string | null>>,
): void {
  setUrl((prev) => {
    if (prev !== null) URL.revokeObjectURL(prev);
    return next === null ? null : URL.createObjectURL(next);
  });
  setFile(next);
}

/**
 * 手动创建条目.
 *
 * 番号必填, 类型可留空 (按番号推断). 其余字段与详情页可编辑项同口径; 只传填过的键,
 * 空串按「没填」处理, 免得把空标题之类写进库.
 *
 * 封面走上传: 默认与海报同图且不裁切 —— 手建条目时用户给的往往就是竖版海报本身,
 * 再裁一次会把画面切掉. 需要裁切时由用户自己打开开关, 或者在本地先手动裁好.
 */
export function ManualCreateDialog({ opened, onClose, initialNumber }: ManualCreateDialogProps) {
  const { t } = useTranslation(["tasks", "common", "metadata"]);
  const qc = useQueryClient();
  // 番号用 ``initialNumber`` 一次性初始化, 不做渲染期派生: 派生写法会让用户删空后又弹回原值,
  // 表现为「删不掉」.
  const [number, setNumber] = useState(() => initialNumber ?? "");
  const [contentType, setContentType] = useState<ContentType | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [cover, setCover] = useState<File | null>(null);
  const [cropPoster, setCropPoster] = useState(false);
  // 手动裁切的开关. 只对本地选中的封面生效 —— 裁完直接换掉 cover, 不碰服务端.
  const [cropOpen, setCropOpen] = useState(false);
  const [cropping, setCropping] = useState(false);

  // 按内容类型分组的「来源 → 固定厂牌」. 候选范围就是类型路由, 所以糖心只在国产下出现.
  const { data: sourcePresets } = useQuery({ ...getSourcePresetsOptions(), enabled: opened });
  const sourcesForType: Record<string, string> = contentType
    ? (sourcePresets?.[contentType] ?? {})
    : {};
  // 标签 / 演员的候选是库里存的**原始名**, 不是映射后的译文.
  const tagOptions = useMappingCandidates("tag", opened);
  const actorOptions = useMappingCandidates("actor", opened);
  // 片商三类共用一张表, 按分类各取一份.
  const studioOptions = useMappingCandidates("vendor", opened, "studio");
  const publisherOptions = useMappingCandidates("vendor", opened, "publisher");
  const seriesOptions = useMappingCandidates("vendor", opened, "series");

  /**
   * 封面的预览地址.
   *
   * 与 `cover` 分开存: blob URL 是外部资源, 必须显式创建与撤销. 早先写成
   * `useMemo(() => URL.createObjectURL(cover), [cover])` 再配一个按地址撤销的 effect,
   * `cover` 换掉时旧地址立刻失效, 而裁切弹窗可能正用旧地址加载图片 —— 表现为裁完封面变空白.
   *
   * 现在只在**换图的动作里**创建地址, 因此地址的生命周期与那次选择一致.
   */
  const [coverPreview, setCoverPreview] = useState<string | null>(null);
  /** 手动裁出的海报; 为空表示海报就是封面本身. */
  const [poster, setPoster] = useState<File | null>(null);
  const [posterPreview, setPosterPreview] = useState<string | null>(null);

  /** 换封面. 海报是独立资源, 换封面不动它 —— 两者各自有自己的 × 可以用来删. */
  const replaceCover = (next: File | null) => setPreview(next, setCover, setCoverPreview);

  /** 换海报 (手动裁切的结果). 只影响海报, 封面保持原样. */
  const replacePoster = (next: File | null) => setPreview(next, setPoster, setPosterPreview);

  // 卸载时释放最后两个地址.
  useEffect(
    () => () => {
      for (const release of [setCoverPreview, setPosterPreview]) {
        release((prev) => {
          if (prev !== null) URL.revokeObjectURL(prev);
          return null;
        });
      }
    },
    [],
  );

  // 弹窗每次重新打开时按当前任务的番号重置; 关闭时 reset 已经清过, 这里覆盖「换一条任务再打开」.
  const openedWith = useRef<string | null>(null);
  useEffect(() => {
    if (!opened) {
      openedWith.current = null;
      return;
    }
    if (openedWith.current === null) {
      openedWith.current = initialNumber ?? "";
      setNumber(initialNumber ?? "");
    }
  }, [opened, initialNumber]);

  const reset = () => {
    setNumber("");
    setContentType(null);
    setSource(null);
    setDraft(EMPTY);
    replaceCover(null);
    setCropPoster(false);
  };

  const close = () => {
    reset();
    onClose();
  };

  /** 切换内容类型: 来源列表跟着换, 已选的来源若不在新类型里就清掉. */
  const onContentTypeChange = (value: ContentType | null) => {
    setContentType(value);
    const allowed: Record<string, string> = value ? (sourcePresets?.[value] ?? {}) : {};
    setSource((current) => (current && allowed[current] ? current : null));
  };

  /**
   * 切换来源: 把该源固定的制作商与发行商都填上.
   *
   * 每次都覆盖 —— 用户换来源就是想换这一组厂牌, 保留上一个来源的值只会留下错的.
   */
  const onSourceChange = (value: string | null) => {
    setSource(value);
    if (!value) return;
    const preset = sourcesForType[value];
    if (!preset) return;
    setDraft((prev) => ({ ...prev, studio: preset, publisher: preset }));
  };

  /**
   * 输入框 onChange 的通用处理器.
   *
   * 必须**先取值再 setState**: ``event.currentTarget`` 只在事件分发期间有效, 分发结束就被置为
   * null, 而 ``setDraft`` 的更新函数是延迟执行的 —— 在更新函数里读 ``currentTarget.value``
   * 会抛 ``Cannot read properties of null``, 表现为输入框完全无法输入.
   */
  const set =
    (key: keyof Draft) => (event: { currentTarget: HTMLInputElement | HTMLTextAreaElement }) => {
      const { value } = event.currentTarget;
      setDraft((prev) => ({ ...prev, [key]: value }));
    };

  const uploadThumb = useMutation(uploadThumbMutation());
  const uploadPoster = useMutation(uploadPosterMutation());

  const create = useMutation({
    ...createMetadataMutation(),
    onSuccess: () => {
      void qc.invalidateQueries();
      notifications.show({ message: t("create.created"), color: "blue" });
      close();
    },
    onError: (err) => {
      notifications.show({
        message: extractErrorMessage(err, t("create.failed")),
        color: "red",
      });
    },
  });

  const submit = async () => {
    const trimmed = number.trim();
    if (!trimmed) {
      notifications.show({ message: t("create.numberRequired"), color: "orange" });
      return;
    }

    const fields: MetadataPartial = {};
    if (draft.title.trim()) fields.title = draft.title.trim();
    if (draft.original_title.trim()) fields.original_title = draft.original_title.trim();
    if (draft.plot.trim()) fields.plot = draft.plot.trim();
    if (draft.release.trim()) fields.release = draft.release.trim();
    if (draft.runtime.trim()) {
      const runtime = Number(draft.runtime.trim());
      if (!Number.isFinite(runtime) || runtime <= 0) {
        notifications.show({ message: t("create.runtimeInvalid"), color: "orange" });
        return;
      }
      fields.runtime = Math.round(runtime);
    }
    if (draft.studio.trim()) fields.studio = draft.studio.trim();
    if (draft.publisher.trim()) fields.publisher = draft.publisher.trim();
    if (draft.series.trim()) fields.series = draft.series.trim();
    if (draft.actors.length > 0) fields.actors = draft.actors;
    if (draft.directors.length > 0) fields.directors = draft.directors;
    if (draft.tags.length > 0) fields.tags = draft.tags;

    const created = await create.mutateAsync({
      body: { number: trimmed, content_type: contentType, source, fields },
    });
    const id = created?.id;
    if (typeof id !== "number") return;

    // 封面与海报各自独立上传: 只给了海报 (或删掉封面只留海报) 也该传上去.
    if (cover) {
      await uploadThumb.mutateAsync({ path: { metadata_id: id }, body: cover });
    }
    /**
     * 海报与封面是两个资源.
     *
     * - 手动裁过 / 单独传过: 上传那一张, 不再让服务端裁 (crop=false), 否则会二次裁切.
     * - 没单独的海报: 海报默认就是封面同图; 开了「自动裁切海报」才交给服务端按比例裁.
     */
    const posterBody = poster ?? cover;
    if (posterBody) {
      await uploadPoster.mutateAsync({
        path: { metadata_id: id },
        body: posterBody,
        query: { crop: poster === null && cropPoster },
      });
    }
    void qc.invalidateQueries();
  };

  const busy = create.isPending || uploadThumb.isPending || uploadPoster.isPending;

  return (
    <Modal
      opened={opened}
      onClose={close}
      title={t("create.title")}
      size="min(94vw, 46rem)"
      centered
      zIndex={Z_INDEX.cropTypes}
    >
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          {t("create.hint")}
        </Text>

        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm">
          <ClearableTextInput
            label={t("create.number")}
            placeholder={t("create.numberPlaceholder")}
            value={number}
            // 取值在事件处理器里就地完成, 不延迟到 setState 的更新函数 (见 set 的注释).
            onChange={(event) => {
              const { value } = event.currentTarget;
              setNumber(value);
            }}
            withAsterisk
            data-autofocus
          />
          <Select
            label={t("create.contentType")}
            placeholder={t("create.contentTypePlaceholder")}
            data={CONTENT_TYPES.map((type) => ({
              value: type,
              label: t(`metadata:search.contentTypes.${type}`),
            }))}
            value={contentType}
            onChange={(value) => onContentTypeChange((value as ContentType) ?? null)}
            clearable
            // 点标签也会让输入框获焦; 默认 openOnFocus 会因此弹出下拉。
            openOnFocus={false}
            // 下拉挂在 body 上, 用 Mantine 默认的 200 会被抬高的弹窗压住, 点不到.
            comboboxProps={{ zIndex: Z_INDEX.dropdown }}
          />
          <Select
            label={t("create.source")}
            placeholder={t("create.sourcePlaceholder")}
            // 先选类型才亮: 来源是类型的细分, 没有类型时无从预填.
            disabled={contentType == null}
            data={Object.entries(sourcesForType).map(([site, publisher]) => ({
              value: site,
              label: `${site} · ${publisher}`,
            }))}
            value={source}
            onChange={onSourceChange}
            clearable
            openOnFocus={false}
            comboboxProps={{ zIndex: Z_INDEX.dropdown }}
          />
        </SimpleGrid>
        <Text size="xs" c="dimmed">
          {t("create.contentTypeHint")}
        </Text>

        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          {/* 原标题在前: 站点给的原文才是一手信息, 标题通常就是它 (或它的译文).
              填原标题时若标题还空着, 顺手把标题也填上, 省一次重复输入. */}
          <ClearableTextInput
            label={t("create.originalTitle")}
            value={draft.original_title}
            onChange={(event) => {
              const { value } = event.currentTarget;
              setDraft((prev) => ({
                ...prev,
                original_title: value,
                // 只在标题为空时跟随, 不覆盖用户已经改过的标题.
                title: prev.title || value,
              }));
            }}
          />
          <ClearableTextInput
            label={t("create.titleField")}
            value={draft.title}
            onChange={set("title")}
          />
          <ClearableTextInput
            label={t("create.release")}
            placeholder="YYYY-MM-DD"
            value={draft.release}
            onChange={set("release")}
          />
          <ClearableTextInput
            label={t("create.runtime")}
            placeholder={t("create.runtimePlaceholder")}
            value={draft.runtime}
            onChange={set("runtime")}
            inputMode="numeric"
          />
          {/* 片商三类都是唯一值, 用单选补全; 选中后落的是片商表里的原始名. */}
          <VendorInput
            label={t("create.studio")}
            placeholder={t("create.vendorPlaceholder")}
            options={studioOptions}
            value={draft.studio}
            onChange={(next) => setDraft((prev) => ({ ...prev, studio: next }))}
          />
          <VendorInput
            label={t("create.publisher")}
            placeholder={t("create.vendorPlaceholder")}
            options={publisherOptions}
            value={draft.publisher}
            onChange={(next) => setDraft((prev) => ({ ...prev, publisher: next }))}
          />
          <VendorInput
            label={t("create.series")}
            placeholder={t("create.vendorPlaceholder")}
            options={seriesOptions}
            value={draft.series}
            onChange={(next) => setDraft((prev) => ({ ...prev, series: next }))}
          />
          {/* 标签与演员: 候选是库里已有的原始名, 直接点选; 没有的名字按回车加入本次录入.
              只动本地表单, 点「创建」才落库. */}
          <CreatableMultiSelect
            label={t("create.tags")}
            placeholder={t("create.tagsPlaceholder")}
            options={tagOptions}
            value={draft.tags}
            onChange={(value) => setDraft((prev) => ({ ...prev, tags: value }))}
          />
          <CreatableMultiSelect
            label={t("create.actors")}
            placeholder={t("create.actorsPlaceholder")}
            options={actorOptions}
            value={draft.actors}
            onChange={(value) => setDraft((prev) => ({ ...prev, actors: value }))}
          />
          <CreatableMultiSelect
            label={t("create.directors")}
            placeholder={t("create.directorsPlaceholder")}
            options={[]}
            value={draft.directors}
            onChange={(value) => setDraft((prev) => ({ ...prev, directors: value }))}
          />
        </SimpleGrid>

        <ClearableTextarea
          label={t("create.plot")}
          autosize
          minRows={3}
          maxRows={8}
          value={draft.plot}
          onChange={set("plot")}
        />

        <Stack gap={6}>
          <Text size="sm" fw={500}>
            {t("create.cover")}
          </Text>
          {/*
            一个「上传图片」入口, 悬停或点击展开两项: 封面与海报是两个不同的资源, 各走各的.
            按钮本身不直接选文件 —— 挂了 FileButton 就分不清用户到底想传哪一个.
          */}
          <Group gap="xs" align="center">
            <Menu
              position="bottom-start"
              shadow="md"
              closeOnItemClick={false}
              // 悬停或点击都能展开; click-hover 比纯 hover 多一条键盘可达路径.
              trigger="click-hover"
              // 弹窗本身已抬到 cropTypes, Menu 默认的 200 会被它盖住.
              zIndex={Z_INDEX.dropdown}
            >
              <Menu.Target>
                <Button variant="light" leftSection={<IconPhotoUp size={16} />} size="xs">
                  {t("create.uploadImage")}
                </Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item
                  leftSection={<IconPhotoUp size={14} />}
                  onClick={(event) => event.stopPropagation()}
                >
                  <FileButton
                    onChange={(file) => {
                      if (!file) return;
                      if (
                        validateCover(
                          file,
                          t("metadata:detail.upload.unsupported"),
                          t("metadata:detail.upload.tooLarge"),
                        )
                      ) {
                        replaceCover(file);
                      }
                    }}
                    accept={ACCEPTED.join(",")}
                  >
                    {(props) => (
                      <span {...props}>
                        {/* 已有封面时这一项是「更换封面」, 否则是「上传封面」. */}
                        {cover ? t("create.changeCoverItem") : t("create.uploadCoverItem")}
                      </span>
                    )}
                  </FileButton>
                </Menu.Item>
                <Menu.Item
                  leftSection={<IconCrop size={14} />}
                  onClick={(event) => event.stopPropagation()}
                >
                  <FileButton
                    onChange={(file) => {
                      if (!file) return;
                      if (
                        validateCover(
                          file,
                          t("metadata:detail.upload.unsupported"),
                          t("metadata:detail.upload.tooLarge"),
                        )
                      ) {
                        replacePoster(file);
                      }
                    }}
                    accept={ACCEPTED.join(",")}
                  >
                    {(props) => (
                      <span {...props}>
                        {/* 已有海报 (上传或手动裁切) 时这一项是「更换海报」. */}
                        {poster ? t("create.changePosterItem") : t("create.uploadPosterItem")}
                      </span>
                    )}
                  </FileButton>
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
            {/* 与上传同款; 没封面时置灰. */}
            <Button
              variant="light"
              leftSection={<IconCrop size={16} />}
              size="xs"
              disabled={!cover}
              onClick={() => setCropOpen(true)}
            >
              {t("create.manualCrop")}
            </Button>
          </Group>
          {/* 封面与海报并排: 手动裁过后两者内容不同, 要让用户同时看到各自是什么. */}
          {(coverPreview || posterPreview) && (
            <Group gap="md" align="flex-start">
              {coverPreview && (
                <Stack gap={4} align="flex-start">
                  <Text size="xs" c="dimmed">
                    {t("create.coverPreview")}
                  </Text>
                  {/* 移除用角标按钮: 整张图可点会被裁切弹窗的关闭点击误触. */}
                  <div style={{ position: "relative" }}>
                    <Image src={coverPreview} alt="" h={160} w="auto" fit="contain" radius="sm" />
                    <ActionIcon
                      variant="filled"
                      color="dark"
                      size="sm"
                      radius="xl"
                      aria-label={t("create.removeCover")}
                      onClick={() => replaceCover(null)}
                      style={{ position: "absolute", top: 4, right: 4 }}
                    >
                      <IconX size={14} />
                    </ActionIcon>
                  </div>
                </Stack>
              )}
              {posterPreview && (
                <Stack gap={4} align="flex-start">
                  <Text size="xs" c="dimmed">
                    {t("create.posterPreview")}
                  </Text>{" "}
                  <div style={{ position: "relative" }}>
                    <Image src={posterPreview} alt="" h={160} w="auto" fit="contain" radius="sm" />
                    <ActionIcon
                      variant="filled"
                      color="dark"
                      size="sm"
                      radius="xl"
                      aria-label={t("create.resetPoster")}
                      onClick={() => replacePoster(null)}
                      style={{ position: "absolute", top: 4, right: 4 }}
                    >
                      <IconX size={14} />
                    </ActionIcon>
                  </div>
                </Stack>
              )}
            </Group>
          )}
          <Text size="xs" c="dimmed">
            {t("create.coverHint")}
          </Text>
          <Switch
            label={t("create.cropPoster")}
            description={t("create.cropPosterHint")}
            checked={cropPoster}
            onChange={(event) => setCropPoster(event.currentTarget.checked)}
            // 手动裁过海报后, 自动裁切不再适用 (会二次裁切); 两者互斥.
            disabled={cover == null || poster !== null}
          />
        </Stack>

        <Group justify="flex-end" gap="xs">
          <Button variant="default" onClick={close} disabled={busy}>
            {t("common:actions.cancel")}
          </Button>
          <Button onClick={() => void submit()} loading={busy}>
            {t("create.submit")}
          </Button>
        </Group>
      </Stack>

      {/* 手动裁切只作用于本地选中的封面: 裁好直接换掉 cover, 点「创建」时上传的就是裁好的图. */}
      {coverPreview && (
        <ImageCropDialog
          opened={cropOpen}
          onClose={() => setCropOpen(false)}
          imageUrl={coverPreview}
          defaultAspect={POSTER_ASPECT}
          initialAnchor="center"
          labels={{
            title: t("create.cropDialogTitle"),
            confirm: t("create.cropConfirm"),
            needSelection: t("metadata:detail.cropPoster.needSelection"),
            operationFailed: t("create.cropFailed"),
            aspect: t("metadata:detail.cropPoster.aspect"),
            lockAspect: t("metadata:detail.cropPoster.lockAspect"),
            left: t("metadata:detail.cropPoster.left"),
            top: t("metadata:detail.cropPoster.top"),
            width: t("metadata:detail.cropPoster.width"),
            height: t("metadata:detail.cropPoster.height"),
            cancel: t("common:actions.cancel"),
            rotateLeft: t("metadata:detail.cropPoster.rotateLeft"),
            rotateRight: t("metadata:detail.cropPoster.rotateRight"),
            resetRotation: t("metadata:detail.cropPoster.resetRotation"),
          }}
          submitting={cropping}
          zIndex={Z_INDEX.cropDialog}
          onSubmit={(box, rotation) => {
            if (cover === null) return;
            setCropping(true);
            void (async () => {
              try {
                const blob = await cropFileToBlob(cover, box, rotation);
                // 裁切结果写进**海报**, 封面保持原样: 两者本来就是不同的资源.
                const base = cover.name.replace(/\.[^.]+$/, "");
                replacePoster(new File([blob], `${base}-poster.png`, { type: "image/png" }));
                setCropOpen(false);
              } catch (err) {
                notifications.show({
                  message: extractErrorMessage(err, t("create.cropFailed")),
                  color: "red",
                });
              } finally {
                setCropping(false);
              }
            })();
          }}
        />
      )}
    </Modal>
  );
}

/** 选图校验: 类型与大小, 与详情页上传同一口径. */
function validateCover(file: File, unsupported: string, tooLarge: string): boolean {
  if (!ACCEPTED.includes(file.type as (typeof ACCEPTED)[number])) {
    notifications.show({ message: unsupported, color: "red" });
    return false;
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    notifications.show({ message: tooLarge, color: "red" });
    return false;
  }
  return true;
}
