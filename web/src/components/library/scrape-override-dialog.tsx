import {
  Alert,
  Button,
  Card,
  Group,
  Image,
  Input,
  Modal,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ContentType, MediaFileResponse } from "@/client/types.gen";
import { EnumToggle } from "@/components/common/enum-toggle";
import { CONTENT_TYPES } from "@/lib/exhaustive-maps";
import { apiFetch } from "@/lib/api-token";
import { proxyImageUrl } from "@/lib/utils";

const CONTENT_TYPE_OPTIONS = ["", ...CONTENT_TYPES] as const;

type ContentTypeChoice = (typeof CONTENT_TYPE_OPTIONS)[number];

interface ScrapeOverrideDialogProps {
  target: MediaFileResponse | null;
  saving: boolean;
  onClose: () => void;
  onSubmit: (
    number: string,
    contentType: ContentType | undefined,
    theporndbUuid: string | undefined,
  ) => void;
}

export function ScrapeOverrideDialog({
  target,
  saving,
  onClose,
  onSubmit,
}: ScrapeOverrideDialogProps) {
  const { t } = useTranslation(["library", "common"]);

  return (
    <Modal opened={target != null} onClose={onClose} title={t("scrapeOverride.title")} size="lg">
      {target != null && (
        <ScrapeOverrideForm
          key={target.id}
          initialNumber={target.number ?? ""}
          saving={saving}
          onClose={onClose}
          onSubmit={onSubmit}
        />
      )}
    </Modal>
  );
}

function ScrapeOverrideForm({
  initialNumber,
  saving,
  onClose,
  onSubmit,
}: {
  initialNumber: string;
  saving: boolean;
  onClose: () => void;
  onSubmit: (
    number: string,
    contentType: ContentType | undefined,
    theporndbUuid: string | undefined,
  ) => void;
}) {
  const { t } = useTranslation(["library", "common"]);
  const [number, setNumber] = useState(initialNumber);
  const [contentType, setContentType] = useState<ContentTypeChoice>("");
  const [theporndbUuid, setTheporndbUuid] = useState("");
  const initialSearch = splitSceneSearch(initialNumber);
  const [sceneQuery, setSceneQuery] = useState(initialSearch.title);
  const [sceneDate, setSceneDate] = useState(initialSearch.date);
  const [sceneError, setSceneError] = useState<string | null>(null);
  const sceneSearch = useMutation({
    mutationFn: async () => {
      if (!sceneQuery.trim() && !sceneDate.trim()) {
        throw new Error(t("scrapeOverride.sceneSearch.emptyQuery"));
      }
      const response = await apiFetch("/api/theporndb-scenes/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: sceneQuery.trim(), date: sceneDate.trim() }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(body?.detail ?? response.statusText);
      }
      return response.json() as Promise<{ items: SceneSearchItem[] }>;
    },
    onError: (error) => setSceneError(error.message),
    onSuccess: () => setSceneError(null),
  });
  const trimmed = number.trim();
  const trimmedThePornDBUuid = theporndbUuid.trim();

  return (
    <Stack>
      <TextInput
        label={t("scrapeOverride.number")}
        placeholder={t("scrapeOverride.numberPlaceholder")}
        value={number}
        onChange={(event) => setNumber(event.currentTarget.value)}
        data-autofocus
      />
      <Input.Wrapper
        label={t("scrapeOverride.contentType")}
        description={t("scrapeOverride.contentTypeHint")}
      >
        <EnumToggle
          fullWidth
          options={CONTENT_TYPE_OPTIONS}
          value={contentType}
          onChange={setContentType}
          getLabel={(value) =>
            value === ""
              ? t("scrapeOverride.contentTypeAuto")
              : t(`scrapeOverride.contentTypes.${value}`)
          }
        />
      </Input.Wrapper>
      {contentType === "western" && (
        <TextInput
          label={t("scrapeOverride.theporndbUuid")}
          placeholder={t("scrapeOverride.theporndbUuidPlaceholder")}
          value={theporndbUuid}
          onChange={(event) => setTheporndbUuid(event.currentTarget.value)}
        />
      )}
      {contentType === "western" && (
        <Stack gap="xs">
          <Text fw={600}>{t("scrapeOverride.sceneSearch.title")}</Text>
          <Group grow align="end">
            <TextInput
              label={t("scrapeOverride.sceneSearch.titleField")}
              placeholder={t("scrapeOverride.sceneSearch.queryPlaceholder")}
              value={sceneQuery}
              onChange={(event) => setSceneQuery(event.currentTarget.value)}
            />
            <TextInput
              label={t("scrapeOverride.sceneSearch.date")}
              placeholder="YYYY.MM.DD"
              value={sceneDate}
              onChange={(event) => setSceneDate(event.currentTarget.value)}
            />
            <Button loading={sceneSearch.isPending} onClick={() => sceneSearch.mutate()}>
              {t("scrapeOverride.sceneSearch.search")}
            </Button>
          </Group>
          {sceneError && <Alert color="red">{sceneError}</Alert>}
          {sceneSearch.isSuccess && sceneSearch.data.items.length === 0 && (
            <Text c="dimmed" size="sm">
              {t("scrapeOverride.sceneSearch.noResults")}
            </Text>
          )}
          {sceneSearch.data?.items && sceneSearch.data.items.length > 0 && (
            <SimpleGrid cols={2} spacing="sm">
              {sceneSearch.data.items.map((scene) => (
                <Card key={scene.id} withBorder padding="xs" radius="sm">
                  <UnstyledButton
                    onClick={() => setTheporndbUuid(scene.id)}
                    aria-label={t("scrapeOverride.sceneSearch.select", { title: scene.title })}
                  >
                    <Image
                      src={proxyImageUrl(scene.image, "theporndb")}
                      h={112}
                      fit="cover"
                      fallbackSrc=""
                    />
                  </UnstyledButton>
                  <Text size="sm" fw={600} lineClamp={1} title={scene.title}>
                    {scene.title || "—"}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {t("scrapeOverride.sceneSearch.dateLabel")}: {scene.date || "—"}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {t("scrapeOverride.sceneSearch.studio")}: {scene.studio || "—"}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {t("scrapeOverride.sceneSearch.duration")}:{" "}
                    {formatSceneDuration(scene.duration)}
                  </Text>
                  <Text size="xs" c="dimmed" lineClamp={1} title={scene.id}>
                    UUID:{" "}
                    {scene.id.length > 12
                      ? `${scene.id.slice(0, 8)}…${scene.id.slice(-4)}`
                      : scene.id}
                  </Text>
                </Card>
              ))}
            </SimpleGrid>
          )}
        </Stack>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose} disabled={saving}>
          {t("common:actions.cancel")}
        </Button>
        <Button
          loading={saving}
          disabled={trimmed === ""}
          onClick={() =>
            onSubmit(
              trimmed,
              contentType === "" ? undefined : contentType,
              contentType === "western" && trimmedThePornDBUuid !== ""
                ? trimmedThePornDBUuid
                : undefined,
            )
          }
        >
          {t("scrapeOverride.submit")}
        </Button>
      </Group>
    </Stack>
  );
}

type SceneSearchItem = {
  id: string;
  title: string;
  date: string;
  duration: number | null;
  studio: string;
  image: string | null;
};

function splitSceneSearch(value: string): { title: string; date: string } {
  const match = value.trim().match(/^(.*?)[. _-](\d{2})[. _-](\d{2})[. _-](\d{2})$/);
  if (!match) return { title: value.trim(), date: "" };
  return {
    title: match[1]!.replace(/[. _-]+$/, ""),
    date: `20${match[2]}.${match[3]}.${match[4]}`,
  };
}

function formatSceneDuration(value: number | null): string {
  if (typeof value !== "number" || value <= 0) return "—";
  const minutes = Math.floor(value / 60);
  return `${minutes} min`;
}
