import {
  Button,
  Alert,
  Box,
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
import { IconAlertCircle, IconCrop, IconPhoto } from "@tabler/icons-react";
import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { isHidden, resolveSchema } from "@/components/schema-form/schema";
import { SchemaForm } from "@/components/schema-form/schema-form";
import { type CropRule, CropRulesDialog } from "@/components/settings/crop-rules-dialog";
import { CropTypesDialog } from "@/components/settings/crop-types-dialog";
import { useConfig, useConfigSchema, useUpdateConfig } from "@/hooks/use-config";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { extractErrorMessage } from "@/lib/api-error";

const settingsSearchSchema = z.object({
  section: z.string().optional(),
});

export const Route = createFileRoute("/settings")({
  validateSearch: settingsSearchSchema,
  component: SettingsPage,
});

/**
 * 总开关右侧的「裁切类型」按钮.
 *
 * `enabled` 取开关的当前值而不是已保存的配置: 关掉总开关时没有可放行的类型, 按钮立刻置灰.
 */
function CropTypesButton({ enabled, onClick }: { enabled: boolean; onClick: () => void }) {
  const { t } = useTranslation("settings");
  return (
    <Button
      size="xs"
      variant="light"
      leftSection={<IconPhoto size={14} />}
      disabled={!enabled}
      onClick={onClick}
    >
      {t("cropTypes.button")}
    </Button>
  );
}

function SettingsPage() {
  const { t } = useTranslation("settings");
  const { section } = Route.useSearch();
  const navigate = Route.useNavigate();
  const narrow = useNarrowViewport("md");
  const { data: schema, isLoading: schemaLoading, error: schemaError } = useConfigSchema();
  const { data: values, isLoading: valuesLoading, error: valuesError } = useConfig();
  const saveMutation = useUpdateConfig();
  const [cropRulesOpen, setCropRulesOpen] = useState(false);
  const [cropTypesOpen, setCropTypesOpen] = useState(false);

  const cropRules = useMemo<CropRule[]>(() => {
    const raw = (values as { scraping?: { poster_crop_rules?: CropRule[] } } | undefined)?.scraping
      ?.poster_crop_rules;
    return Array.isArray(raw) ? raw : [];
  }, [values]);

  const cropTypes = useMemo<string[]>(() => {
    const raw = (values as { scraping?: { poster_crop_types?: string[] } } | undefined)?.scraping
      ?.poster_crop_types;
    return Array.isArray(raw) ? raw : [];
  }, [values]);

  // 可选类型取类型路由的键: 新增类型路由时列表自动跟上, 不在这里另写一份枚举.
  const cropTypeOptions = useMemo<string[]>(() => {
    const routes = (
      values as { scraping?: { content_routes?: Record<string, unknown> } } | undefined
    )?.scraping?.content_routes;
    return routes ? Object.keys(routes) : [];
  }, [values]);

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
                  <SchemaForm
                    schema={sectionSchema}
                    prefix={key}
                    values={sectionValues}
                    i18nPrefix="settings:fields"
                    actionsPlacement="affix"
                    onSave={(patch) => saveMutation.mutate({ body: { [key]: patch } })}
                    saving={saveMutation.isPending}
                    afterField={
                      key === "scraping"
                        ? {
                            poster_crop_detect_face: (
                              <Group justify="space-between" wrap="nowrap" gap="md" py="md">
                                <Box style={{ minWidth: 0 }}>
                                  <Text size="sm" fw={600}>
                                    {t("cropRules.title")}
                                  </Text>
                                  <Text size="xs" c="dimmed">
                                    {t("cropRules.description")}
                                  </Text>
                                </Box>
                                <Button
                                  size="xs"
                                  variant="light"
                                  leftSection={<IconCrop size={14} />}
                                  onClick={() => setCropRulesOpen(true)}
                                >
                                  {t("cropRules.button")}
                                </Button>
                              </Group>
                            ),
                          }
                        : undefined
                    }
                    trailingField={
                      key === "scraping"
                        ? {
                            // 类型开关贴着总开关: 它细化的正是「裁切海报」放行哪些类型.
                            crop_poster: {
                              render: (enabled: boolean) => (
                                <CropTypesButton
                                  enabled={enabled}
                                  onClick={() => setCropTypesOpen(true)}
                                />
                              ),
                            },
                          }
                        : undefined
                    }
                  />
                </Paper>
              );
            })}
          </Box>
        </Group>
      )}

      <CropRulesDialog
        opened={cropRulesOpen}
        onClose={() => setCropRulesOpen(false)}
        rules={cropRules}
        onChange={(next) =>
          saveMutation.mutate({ body: { scraping: { poster_crop_rules: next } } })
        }
      />

      <CropTypesDialog
        opened={cropTypesOpen}
        onClose={() => setCropTypesOpen(false)}
        selected={cropTypes}
        options={cropTypeOptions}
        onChange={(next) =>
          saveMutation.mutate({ body: { scraping: { poster_crop_types: next } } })
        }
      />
    </Stack>
  );
}
