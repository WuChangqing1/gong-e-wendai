/** 管理员：用户管理（基础能力）。 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { App as AntdApp, Button, Input, Space, Table, Tag } from 'antd';

import { adminApi } from '@/api/consultation';
import { errorMessage } from '@/api/client';
import { InlineNote, PageHeader, SectionCard, StatusTag } from '@/components/ui';
import { formatDateTime } from '@/utils/datetime';
import { ROLE_LABELS } from '@/utils/labels';
import type { Role } from '@/types';

interface AdminUser {
  id: string;
  username: string;
  display_name: string;
  roles: string[];
  status: string;
  created_at: string;
  last_login_at: string | null;
}

export default function AdminUsersPage() {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');

  const usersQuery = useQuery({
    queryKey: ['admin', 'users', page, search],
    queryFn: () => adminApi.users({ page, page_size: 15, search: search || undefined }),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'active' | 'disabled' }) =>
      adminApi.setUserStatus(id, status),
    onSuccess: () => {
      message.success('账户状态已更新');
      queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  return (
    <div className="gew-stack">
      <PageHeader
        title="用户管理"
        subtitle="查看账户与业务角色，必要时停用异常账户。管理员不能查看商户的经营明细。"
      />

      <SectionCard>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input.Search
            allowClear
            placeholder="搜索账户名或称呼"
            style={{ width: 240 }}
            onSearch={(value) => {
              setSearch(value);
              setPage(1);
            }}
          />
        </Space>

        <Table<AdminUser>
          rowKey="id"
          size="middle"
          loading={usersQuery.isLoading}
          dataSource={usersQuery.data?.items ?? []}
          scroll={{ x: 860 }}
          columns={[
            { title: '账户名', dataIndex: 'username', render: (value: string) => <span className="num">{value}</span> },
            { title: '称呼', dataIndex: 'display_name' },
            {
              title: '业务角色',
              dataIndex: 'roles',
              render: (roles: string[]) => (
                <Space size={4} wrap>
                  {roles.map((role) => (
                    <Tag key={role} bordered={false}>
                      {ROLE_LABELS[role as Role] ?? role}
                    </Tag>
                  ))}
                </Space>
              ),
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 110,
              render: (value: string) =>
                value === 'active' ? <StatusTag tone="ok">正常</StatusTag> : <StatusTag tone="neutral">已停用</StatusTag>,
            },
            {
              title: '注册时间',
              dataIndex: 'created_at',
              width: 170,
              render: (value: string) => formatDateTime(value),
            },
            {
              title: '最近登录',
              dataIndex: 'last_login_at',
              width: 170,
              render: (value: string | null) => (value ? formatDateTime(value) : '—'),
            },
            {
              title: '操作',
              key: 'actions',
              width: 120,
              render: (_value: unknown, row: AdminUser) => (
                <Button
                  size="small"
                  danger={row.status === 'active'}
                  loading={statusMutation.isPending}
                  onClick={() =>
                    statusMutation.mutate({
                      id: row.id,
                      status: row.status === 'active' ? 'disabled' : 'active',
                    })
                  }
                >
                  {row.status === 'active' ? '停用' : '启用'}
                </Button>
              ),
            },
          ]}
          pagination={{
            current: usersQuery.data?.meta.page ?? 1,
            pageSize: 15,
            total: usersQuery.data?.meta.total ?? 0,
            showSizeChanger: false,
            onChange: setPage,
          }}
        />
      </SectionCard>

      <InlineNote tone="info">
        停用账户会立即阻止其继续登录与访问接口。本页面不展示密码、令牌或任何凭据。
      </InlineNote>
    </div>
  );
}
