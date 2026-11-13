import { ActionIcon, Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { apiFetch } from "@/lib/api-token";
import { Z_INDEX } from "@/lib/z-index";

const BASE = "/api/actor-name-mappings";

interface Entry {
  original: string;
  translation: string | null;
}

interface Result {
  items: Entry[];
  total: number;
  stats: { all: number; pending: number; mapped: number };
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

export interface ActorNameMappingEditorProps {
  opened: boolean;
  onClose: () => void;
  /** 演员的展示名, 也是映射表的键. */
  name: string;
}

/**
 * 单个演员的中文映射编辑.
 *
 * 与列表页的管理弹窗共用同一个端点, 这里只改这一个演员: 详情页看得见原名, 不必再让人
 * 从上千条里挑. 映射只改展示, 搜索与去重仍用原名.
 */
export function ActorNameMappingEditor({ opened, onClose, name }: ActorNameMappingEditorProps) {
  const { t } = useTranslation(["metadata", "common"]);
  const qc = useQueryClient();
  // 打开时回填现有映射: 已经填过的演员不该显示成空白.
  const existing = useQuery({
    queryKey: ["actor-name-mappings", "lookup", name],
    queryFn: async () => {
      const params = new URLSearchParams({ search: name, limit: "50" });
      const result = await request<Result>(`${BASE}?${params}`);
      return result.items.find((item) => item.original === name)?.translation ?? "";
    },
    enabled: opened && Boolean(name),
  });

  const save = useMutation({
    mutationFn: (value: string) =>
      value
        ? request(`${BASE}`, {
            method: "PUT",
            body: JSON.stringify({ original: name, translation: value }),
          })
        : request(`${BASE}/${encodeURIComponent(name)}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["actor-name-mappings"] });
      void qc.invalidateQueries({ queryKey: ["actors"] });
      onClose();
    },
  });

  // 输入框的初值取现有映射. 不用 effect 同步: 那会在每次数据到达时多渲染一轮,
  // 而且用户已经开始输入时会被覆盖掉.
  const [draft, setDraft] = useState<string | null>(null);
  const translation = draft ?? existing.data ?? "";
  const trimmed = translation.trim();

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t("actorNameMapping.detailTitle")}
      size="min(94vw, 30rem)"
      centered
      zIndex={Z_INDEX.base}
    >
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          {t("actorNameMapping.detailHint")}
        </Text>
        <TextInput label={t("actorNameMapping.original")} value={name} readOnly />
        <TextInput
          label={t("actorNameMapping.translation")}
          placeholder={t("actorNameMapping.placeholder")}
          value={translation}
          onChange={(e) => setDraft(e.currentTarget.value)}
          data-autofocus
        />
        <Group justify="flex-end" gap="xs">
          {/* 删除这条映射: 垃圾桶图标放最左, 与右侧的取消 / 保存拉开距离, 免得误点. */}
          <ActionIcon
            variant="light"
            color="red"
            size="lg"
            aria-label={t("actorNameMapping.clear")}
            loading={save.isPending}
            disabled={!existing.data}
            onClick={() => save.mutate("")}
            mr="auto"
          >
            <IconTrash size={16} />
          </ActionIcon>
          <Button variant="default" onClick={onClose}>
            {t("common:actions.cancel")}
          </Button>
          <Button loading={save.isPending} onClick={() => save.mutate(trimmed)}>
            {t("actorNameMapping.save")}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
