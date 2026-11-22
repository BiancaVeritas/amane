import { ActionIcon, Button, Group, Modal, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { apiFetch } from "@/lib/api-token";
import { Z_INDEX } from "@/lib/z-index";
import { ClearableTextInput } from "@/components/common/clearable-input";

/**
 * 单个分类名的中文映射编辑.
 *
 * 演员详情页与分类详情页共用这一个弹窗, 只改当前这一个名字: 看得见原名, 不必去映射表里
 * 从上千条中挑. 映射只改展示, 搜索、去重与刮削仍用原名.
 *
 * 主按钮跟着输入框走: 有字时是「保存」(写入译名), 没字时是「归档」(离开主列表).
 * 两者是同一件事的两面 —— 归档要求没有译名, 所以清空并归档要连着做.
 */
export type MappingGroupId = "tag" | "actor" | "vendor";

/** 片商组的三个分类共用一个端点, 表里靠名字区分, 不需要额外的角色参数. */
const KIND_TO_GROUP: Record<string, MappingGroupId> = {
  tag: "tag",
  actor: "actor",
  studio: "vendor",
  publisher: "vendor",
  series: "vendor",
};

interface Entry {
  original: string;
  translation: string | null;
  archived?: boolean;
}

interface Result {
  items: Entry[];
  total: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export interface NameMappingDialogProps {
  opened: boolean;
  onClose: () => void;
  /** 分类种类 (`FacetKind` 的取值); 决定写进哪张映射表. */
  kind: string;
  /** 原始名, 也是映射表的键. */
  name: string;
}

export function NameMappingDialog({ opened, onClose, kind, name }: NameMappingDialogProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const qc = useQueryClient();
  const group = KIND_TO_GROUP[kind] ?? "tag";
  const base = `/api/mappings/${group}`;

  // 打开时回填现有映射: 已经填过的名字不该显示成空白.
  const existing = useQuery({
    queryKey: ["mappings", group, "lookup", name],
    queryFn: async () => {
      const params = new URLSearchParams({ search: name, limit: "50", status: "all" });
      const result = await request<Result>(`${base}?${params}`);
      return result.items.find((item) => item.original === name) ?? null;
    },
    enabled: opened && Boolean(name),
  });

  const current = existing.data ?? null;
  const archived = current?.archived === true;

  // 输入框的初值取现有映射. 不用 effect 同步: 那会在每次数据到达时多渲染一轮,
  // 而且用户已经开始输入时会被覆盖掉.
  const [draft, setDraft] = useState<string | null>(null);

  /** 关掉弹窗并丢掉草稿; 下次打开重新读回现有映射. */
  const close = () => {
    setDraft(null);
    onClose();
  };

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["mappings"] });
    void qc.invalidateQueries({ queryKey: ["actors"] });
    void qc.invalidateQueries({ queryKey: ["facets"] });
  };

  /** 写入译名; 空值即清空, 词条退回未映射状态 (仍留在主列表). */
  const save = useMutation({
    mutationFn: (value: string) =>
      request<void>(`${base}`, {
        method: "PUT",
        body: JSON.stringify({ original: name, translation: value || null }),
      }),
    // 垃圾桶走的就是这一条 (传空值): 它只是清掉译名, 弹窗留着让人接着改.
    onSuccess: (_, value) => {
      refresh();
      if (value) close();
    },
  });

  /** 归档: 先把译名清空, 再移出主列表; 映射表那边立刻能看到. */
  const archive = useMutation({
    mutationFn: async () => {
      await request<void>(`${base}`, {
        method: "PUT",
        body: JSON.stringify({ original: name, translation: null }),
      });
      await request<void>(`${base}/${encodeURIComponent(name)}`, { method: "DELETE" });
    },
    onSuccess: () => {
      refresh();
      close();
    },
  });

  /** 还原: 状态变了但弹窗留着, 「还原」按下去往往还想接着看或再改. */
  const restore = useMutation({
    mutationFn: () =>
      request<void>(`${base}/restore/${encodeURIComponent(name)}`, { method: "POST" }),
    onSuccess: refresh,
  });

  const translation = draft ?? current?.translation ?? "";
  const trimmed = translation.trim();
  // 有字就是要保存, 没字就是要归档; 已归档的词条只提供还原.
  const mode: "save" | "archive" | "restore" = archived ? "restore" : trimmed ? "save" : "archive";
  const pending = save.isPending || archive.isPending || restore.isPending;

  return (
    <Modal
      opened={opened}
      onClose={close}
      title={t("mappings.detailTitle")}
      size="min(94vw, 30rem)"
      centered
      zIndex={Z_INDEX.base}
    >
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          {t("mappings.detailHint")}
        </Text>
        <TextInput label={t("mappings.original")} value={name} readOnly />
        <ClearableTextInput
          label={t("mappings.translation")}
          placeholder={t("mappings.translationPlaceholder")}
          value={translation}
          onChange={(e) => setDraft(e.currentTarget.value)}
          disabled={archived}
          data-autofocus
        />
        <Group justify="flex-end" gap="xs">
          {/* 清空译名: 把那几个字删掉, 词条退回未映射状态, 仍留在主列表里.
              与右侧按钮拉开距离, 免得和保存 / 归档误点. */}
          <Tooltip label={t("mappings.clearTranslation")} withArrow>
            <ActionIcon
              variant="light"
              color="red"
              size="lg"
              aria-label={t("mappings.clearTranslation")}
              loading={save.isPending}
              disabled={archived || !current?.translation}
              onClick={() => {
                setDraft(null);
                save.mutate("");
              }}
              mr="auto"
            >
              <IconTrash size={16} />
            </ActionIcon>
          </Tooltip>
          <Button variant="default" onClick={onClose}>
            {t("common:actions.cancel")}
          </Button>
          {/* 主按钮随输入切换: 输入文字时是「保存」, 删掉文字时是「归档」. */}
          <Button
            loading={pending}
            color={mode === "save" ? undefined : "red"}
            variant={mode === "save" ? "filled" : "light"}
            onClick={() => {
              if (mode === "restore") restore.mutate();
              else if (mode === "save") save.mutate(trimmed);
              else archive.mutate();
            }}
          >
            {mode === "restore"
              ? t("mappings.restore")
              : mode === "save"
                ? t("mappings.save")
                : t("mappings.archive")}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
