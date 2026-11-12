import {
  Alert,
  Box,
  Button,
  Center,
  Group,
  Loader,
  NavLink,
  Paper,
  Select,
  Stack,
  Text,
  Title,
} from "@mantine/core";
import { IconAlertCircle, IconPlugConnected, IconRefresh } from "@tabler/icons-react";
import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { isHidden, resolveSchema } from "@/components/schema-form/schema";
import { SchemaForm } from "@/components/schema-form/schema-form";
import { useConfig, useConfigSchema, useUpdateConfig } from "@/hooks/use-config";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { extractErrorMessage } from "@/lib/api-error";
import { apiFetch } from "@/lib/api-token";
import { notifications } from "@mantine/notifications";

const settingsSearchSchema = z.object({
  section: z.string().optional(),
});

// Keep these values aligned with ScrapingConfig defaults in src/amane/config/manager.py.
const DEFAULT_CONTENT_ROUTES = {
  censored: ["dmm", "mgstage", "javdb", "avbase", "javbus", "avsox"],
  uncensored: [
    "aventertainments",
    "10musume",
    "1pondo",
    "caribbeancom",
    "pacopacomama",
    "tokyo_hot",
    "caribbeancompr",
    "d2pass",
  ],
  fc2: ["javdb", "fc2ppvdb", "fc2", "freejavbt", "airav"],
  chinese: ["iqqtv", "javdb", "airav", "freejavbt"],
  amateur: ["mgstage", "dmm", "javdb", "javbus"],
  western: ["theporndb"],
  hentai: ["getchu", "dmm", "javdb"],
};

export const Route = createFileRoute("/settings")({
  validateSearch: settingsSearchSchema,
  component: SettingsPage,
});

function SettingsPage() {
  const { t } = useTranslation("settings");
  const { section } = Route.useSearch();
  const navigate = Route.useNavigate();
  const narrow = useNarrowViewport("md");
  const { data: schema, isLoading: schemaLoading, error: schemaError } = useConfigSchema();
  const { data: values, isLoading: valuesLoading, error: valuesError } = useConfig();
  const saveMutation = useUpdateConfig();
  const [testingMediaServer, setTestingMediaServer] = useState(false);

  async function testMediaServer() {
    const mediaServer = (values as Record<string, unknown> | undefined)?.media_server;
    if (!mediaServer) return;
    setTestingMediaServer(true);
    try {
      const response = await apiFetch("/api/config/media-server/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(mediaServer),
      });
      const result = (await response.json().catch(() => ({}))) as {
        detail?: string;
        people_count?: number;
      };
      if (!response.ok) throw new Error(result.detail ?? `HTTP ${response.status}`);
      notifications.show({
        message: t("mediaServer.testSuccess", { count: result.people_count ?? 0 }),
        color: "green",
      });
    } catch (err) {
      notifications.show({
        message: extractErrorMessage(err, t("mediaServer.testFailed")),
        color: "red",
      });
    } finally {
      setTestingMediaServer(false);
    }
  }

  function restoreScrapingDefault(
    field: "poster_ratio" | "uncensored_poster_ratio" | "poster_crop_skip_ratio" | "content_routes",
  ) {
    const defaults = {
      poster_ratio: 0.7,
      uncensored_poster_ratio: 0.87,
      poster_crop_skip_ratio: 0.9,
      content_routes: DEFAULT_CONTENT_ROUTES,
    };
    saveMutation.mutate({ body: { scraping: { [field]: defaults[field] } } });
  }

  const resolvedSchema = useMemo(() => (schema ? resolveSchema(schema, schema) : null), [schema]);

  const sectionKeys = useMemo(() => {
    if (!resolvedSchema?.properties) return [];
    return Object.entries(resolvedSchema.properties)
      .filter(([, value]) => typeof value !== "boolean" && !isHidden(value))
      .map(([key]) => key);
  }, [resolvedSchema]);

  const currentSection =
    (section && sectionKeys.includes(section) ? section : sectionKeys[0]) ?? null;

  const loading = schemaLoading || valuesLoading;
  const error = schemaError ?? valuesError;

  return (
    <Stack gap="md" pb={160}>
      <Title order={2}>{t("title")}</Title>

      {loading && (
        <Center py="xl">
          <Loader size="sm" />
        </Center>
      )}

      {error && (
        <Alert icon={<IconAlertCircle size={18} />} color="red" variant="light">
          {extractErrorMessage(error, t("config.loadError"))}
        </Alert>
      )}

      {!loading && !error && resolvedSchema && values && currentSection && (
        <Group align="flex-start" gap="lg" wrap={narrow ? "wrap" : "nowrap"}>
          {narrow ? (
            // 窄屏侧栏与表单无法并排: 220px 固定导航会把字段压到几十像素宽.
            <Select
              w="100%"
              allowDeselect={false}
              value={currentSection}
              onChange={(key) => {
                if (key) void navigate({ search: { section: key } });
              }}
              data={sectionKeys.map((key) => ({
                value: key,
                // Schema 顶层分组名运行时才知, 动态表单例外.
                label: t(`tabs.${key}` as never, { defaultValue: key }),
              }))}
            />
          ) : (
            <Paper withBorder p="xs" w={220} style={{ position: "sticky", top: 12, flexShrink: 0 }}>
              <Stack gap={4}>
                {sectionKeys.map((key) => (
                  <NavLink
                    key={key}
                    // Schema 顶层分组名运行时才知, 动态表单例外.
                    label={t(`tabs.${key}` as never, { defaultValue: key })}
                    active={currentSection === key}
                    onClick={() => void navigate({ search: { section: key } })}
                    variant="filled"
                    style={{ borderRadius: "var(--mantine-radius-md)" }}
                  />
                ))}
              </Stack>
            </Paper>
          )}

          <Box style={{ flex: 1, minWidth: 0 }}>
            {sectionKeys.map((key) => {
              if (key !== currentSection) return null;
              const sectionSchema = resolvedSchema.properties?.[key];
              if (!sectionSchema || typeof sectionSchema === "boolean") return null;
              // Schema 顶层 key 运行时才知; 桥接到按 section 切分的表单 values.
              const sectionValues = (values as Record<string, Record<string, unknown>>)[key] ?? {};
              return (
                <Paper key={key} withBorder p="md">
                  <Text fw={600} mb="md">
                    {/* Schema 顶层分组名运行时才知, 动态表单例外. */}
                    {t(`tabs.${key}` as never, { defaultValue: key })}
                  </Text>
                  {key === "media_server" ? (
                    <Group justify="flex-end" mb="md">
                      <Button
                        size="sm"
                        variant="light"
                        leftSection={<IconPlugConnected size={16} />}
                        loading={testingMediaServer}
                        onClick={() => void testMediaServer()}
                      >
                        {t("mediaServer.testConnection")}
                      </Button>
                    </Group>
                  ) : null}
                  <SchemaForm
                    schema={sectionSchema}
                    prefix={key}
                    values={sectionValues}
                    i18nPrefix="settings:fields"
                    actionsPlacement="affix"
                    fieldActions={
                      key === "scraping"
                        ? Object.fromEntries(
                            [
                              "poster_ratio",
                              "uncensored_poster_ratio",
                              "poster_crop_skip_ratio",
                              "content_routes",
                            ].map((field) => [
                              field,
                              <Button
                                key={field}
                                size="xs"
                                variant="subtle"
                                leftSection={<IconRefresh size={14} />}
                                loading={saveMutation.isPending}
                                onClick={() =>
                                  restoreScrapingDefault(
                                    field as
                                      | "poster_ratio"
                                      | "uncensored_poster_ratio"
                                      | "poster_crop_skip_ratio"
                                      | "content_routes",
                                  )
                                }
                              >
                                {t("restoreDefault")}
                              </Button>,
                            ]),
                          )
                        : undefined
                    }
                    onSave={(patch) => saveMutation.mutate({ body: { [key]: patch } })}
                    saving={saveMutation.isPending}
                  />
                </Paper>
              );
            })}
          </Box>
        </Group>
      )}
    </Stack>
  );
}
