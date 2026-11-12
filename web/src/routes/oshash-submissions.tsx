import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Pagination,
  Paper,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { apiFetch } from "@/lib/api-token";

export const Route = createFileRoute("/oshash-submissions")({ component: OshashSubmissionsPage });

type OshashSubmission = {
  id: number;
  theporndb_uuid: string;
  oshash: string;
  duration: number;
  title: string | null;
  path: string;
  created_at: string;
  last_error: string | null;
};
type OshashResult = { items: OshashSubmission[]; total: number };
const PAGE_SIZE = 30;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) throw new Error(await response.text());
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function OshashSubmissionsPage() {
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<number[]>([]);
  const anchor = useRef<number | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["oshash-submissions", page],
    queryFn: () =>
      request<OshashResult>(
        `/api/oshash-submissions?offset=${(page - 1) * PAGE_SIZE}&limit=${PAGE_SIZE}`,
      ),
  });
  const submitOne = useMutation({
    mutationFn: (id: number) =>
      request<{ submitted: boolean }>(`/api/oshash-submissions/${id}/submit`, { method: "POST" }),
    onSuccess: (_result, id) => {
      setSelected((old) => old.filter((selectedId) => selectedId !== id));
      setFeedback("OSHASH 提交成功，记录已从待提交列表移除。");
      void qc.invalidateQueries({ queryKey: ["oshash-submissions"] });
    },
    onError: (error) => setFeedback(`提交失败：${String(error)}`),
  });
  const deleteOne = useMutation({
    mutationFn: (id: number) =>
      request<{ deleted: number }>(`/api/oshash-submissions/${id}`, { method: "DELETE" }),
    onSuccess: (_result, id) => {
      setSelected((old) => old.filter((selectedId) => selectedId !== id));
      setFeedback("OSHASH 记录已删除。");
      void qc.invalidateQueries({ queryKey: ["oshash-submissions"] });
    },
    onError: (error) => setFeedback(`删除失败：${String(error)}`),
  });
  const deleteBatch = useMutation({
    mutationFn: (ids: number[]) =>
      request<{ deleted: number }>("/api/oshash-submissions/delete", {
        method: "POST",
        body: JSON.stringify({ ids }),
      }),
    onSuccess: (result) => {
      setSelected([]);
      anchor.current = null;
      setFeedback(`已删除选中的 ${result.deleted} 条记录。`);
      void qc.invalidateQueries({ queryKey: ["oshash-submissions"] });
    },
    onError: (error) => setFeedback(`批量删除失败：${String(error)}`),
  });
  const submitBatch = useMutation({
    mutationFn: (ids: number[]) =>
      request<{ succeeded: number; failed: number; errors: string[] }>(
        "/api/oshash-submissions/batch",
        { method: "POST", body: JSON.stringify({ ids }) },
      ),
    onSuccess: (result) => {
      setSelected([]);
      anchor.current = null;
      setFeedback(
        `批量提交完成：成功 ${result.succeeded} 条，失败 ${result.failed} 条${result.errors.length ? `；${result.errors.join("；")}` : ""}`,
      );
      void qc.invalidateQueries({ queryKey: ["oshash-submissions"] });
    },
    onError: (error) => setFeedback(`批量提交失败：${String(error)}`),
  });
  const total = query.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (query.data && page > totalPages) {
    setPage(totalPages);
  }
  const items = query.data?.items ?? [];
  const pageAllSelected = items.length > 0 && items.every((item) => selected.includes(item.id));
  const pageSomeSelected = items.some((item) => selected.includes(item.id));

  return (
    <Stack p="md" gap="md">
      <Group justify="space-between" align="center">
        <div>
          <Title order={2}>OSHASH 提交管理</Title>
          <Text size="sm" c="dimmed">
            管理通过手动 UUID 刮削进入队列的本地视频；提交成功后自动移除，失败记录保留并显示原因。
          </Text>
        </div>
        <Group gap="xs">
          <Text size="sm" c="dimmed">
            已勾选 {selected.length} 条
          </Text>
          <Button
            disabled={!selected.length || submitBatch.isPending}
            loading={submitBatch.isPending}
            onClick={() => submitBatch.mutate([...selected])}
          >
            批量提交
          </Button>
        </Group>
      </Group>
      {feedback && (
        <Alert
          color={feedback.includes("失败") ? "red" : "green"}
          onClose={() => setFeedback(null)}
          withCloseButton
        >
          {feedback}
        </Alert>
      )}
      {query.isError && <Alert color="red">待提交列表加载失败：{String(query.error)}</Alert>}
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          待提交共 {total} 条 · 第 {page} / {totalPages} 页 · 每页 30 条
        </Text>
        <Group gap="sm">
          <Checkbox
            label="全选当前页"
            checked={pageAllSelected}
            indeterminate={pageSomeSelected && !pageAllSelected}
            onChange={(event) => {
              const checked = event.currentTarget?.checked ?? false;
              const ids = items.map((item) => item.id);
              setSelected((old) =>
                checked ? [...new Set([...old, ...ids])] : old.filter((id) => !ids.includes(id)),
              );
              anchor.current = null;
            }}
          />
          <Button
            color="red"
            variant="light"
            disabled={!selected.length || deleteBatch.isPending}
            loading={deleteBatch.isPending}
            onClick={() => {
              const ids = [...selected];
              if (ids.length && window.confirm(`确定删除已选中的 ${ids.length} 条记录吗？`))
                deleteBatch.mutate(ids);
            }}
          >
            删除
          </Button>
        </Group>
      </Group>
      <Paper withBorder radius="md" style={{ overflowX: "auto" }}>
        <Table striped highlightOnHover miw={900} verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>选择</Table.Th>
              <Table.Th>影片</Table.Th>
              <Table.Th>Scene UUID</Table.Th>
              <Table.Th>OSHASH</Table.Th>
              <Table.Th>时长</Table.Th>
              <Table.Th>状态/错误</Table.Th>
              <Table.Th>操作</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {items.map((item, index) => (
              <Table.Tr key={item.id}>
                <Table.Td>
                  <Checkbox
                    aria-label={`选择 OSHASH ${item.id}`}
                    checked={selected.includes(item.id)}
                    onChange={(event) => {
                      const checked = event.currentTarget?.checked ?? false;
                      const previousAnchor = anchor.current;
                      const anchorIndex =
                        previousAnchor == null
                          ? -1
                          : items.findIndex((row) => row.id === previousAnchor);
                      if ((event.nativeEvent as MouseEvent).shiftKey && anchorIndex >= 0) {
                        const range = items
                          .slice(Math.min(anchorIndex, index), Math.max(anchorIndex, index) + 1)
                          .map((row) => row.id);
                        setSelected((old) =>
                          checked
                            ? [...new Set([...old, ...range])]
                            : old.filter((id) => !range.includes(id)),
                        );
                      } else {
                        anchor.current = item.id;
                        setSelected((old) =>
                          checked
                            ? [...new Set([...old, item.id])]
                            : old.filter((id) => id !== item.id),
                        );
                      }
                    }}
                  />
                </Table.Td>
                <Table.Td>
                  <Text fw={500}>{item.title || item.path.split(/[\\/]/).pop()}</Text>
                  <Text size="xs" c="dimmed" lineClamp={1}>
                    {item.path}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="xs">{item.theporndb_uuid}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="xs" ff="monospace">
                    {item.oshash}
                  </Text>
                </Table.Td>
                <Table.Td>{Math.round(item.duration)} 秒</Table.Td>
                <Table.Td>
                  {item.last_error ? (
                    <Text size="xs" c="red">
                      {item.last_error}
                    </Text>
                  ) : (
                    <Badge color="orange">待提交</Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap="xs">
                    <Button
                      size="xs"
                      loading={submitOne.isPending && submitOne.variables === item.id}
                      disabled={
                        submitBatch.isPending || deleteOne.isPending || deleteBatch.isPending
                      }
                      onClick={() => submitOne.mutate(item.id)}
                    >
                      提交
                    </Button>
                    <Button
                      size="xs"
                      color="red"
                      variant="light"
                      loading={deleteOne.isPending && deleteOne.variables === item.id}
                      disabled={deleteOne.isPending || deleteBatch.isPending}
                      onClick={() => {
                        if (window.confirm("确定删除这条 OSHASH 记录吗？"))
                          deleteOne.mutate(item.id);
                      }}
                    >
                      删除
                    </Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
            {items.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={7}>
                  <Text c="dimmed" ta="center" py="lg">
                    暂无待提交的 OSHASH
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Paper>
      <Group justify="flex-end">
        <Pagination
          total={totalPages}
          value={page}
          onChange={(next) => {
            setPage(next);
            setSelected([]);
            anchor.current = null;
          }}
        />
      </Group>
    </Stack>
  );
}
