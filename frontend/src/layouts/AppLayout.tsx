/** 主布局：PC 左侧导航 + 顶部信息栏；移动端顶部标题 + 底部核心导航。 */

import { useMemo } from 'react';
import { Layout, Menu, Tooltip, Button, Dropdown, Avatar } from 'antd';
import { LogoutOutlined, MenuFoldOutlined, MenuUnfoldOutlined, UserOutlined } from '@ant-design/icons';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';

import { BrandMark } from '@/components/Brand';
import { authApi } from '@/api/auth';
import { queryKeys, queryClient } from '@/api/queryClient';
import { navForRoles } from '@/layouts/navigation';
import { primaryRole, useAuthStore, useUiStore } from '@/store';
import { ROLE_LABELS } from '@/utils/labels';

const { Sider, Header, Content } = Layout;

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAuthStore((state) => state.user);
  const clear = useAuthStore((state) => state.clear);
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const isMobile = useUiStore((state) => state.isMobile);
  const setIsMobile = useUiStore((state) => state.setIsMobile);

  // 窄屏下一律收起左侧导航（导航改由底部导航承担）。
  // 只声明 Sider 的 breakpoint 而不接管 collapsed，窄屏时左侧仍会占用 216px，
  // 把内容区压到 150px 左右，图表与表格都会挤成一列。
  const siderCollapsed = collapsed || isMobile;

  const navItems = useMemo(() => navForRoles(user?.roles ?? []), [user?.roles]);
  const role = primaryRole(user);

  const selectedKey = useMemo(() => {
    const match = navItems
      .filter((item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`))
      .sort((a, b) => b.path.length - a.path.length)[0];
    return match?.key ?? navItems[0]?.key ?? '';
  }, [location.pathname, navItems]);

  const logoutMutation = useMutation({
    mutationFn: () => authApi.logout(),
    onSettled: () => {
      clear();
      queryClient.removeQueries({ queryKey: queryKeys.me });
      queryClient.clear();
      navigate('/login', { replace: true });
    },
  });

  const menuItems = navItems.map((item) => ({
    key: item.key,
    icon: item.icon,
    label: item.label,
    onClick: () => navigate(item.path),
  }));

  const activeItem = navItems.find((item) => item.key === selectedKey);

  return (
    <Layout className="gew-layout">
      <Sider
        className="gew-sider"
        width={216}
        collapsedWidth={64}
        collapsed={siderCollapsed}
        breakpoint="lg"
        trigger={null}
        theme="light"
        onBreakpoint={(broken) => setIsMobile(broken)}
      >
        <BrandMark collapsed={siderCollapsed} />
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          style={{ borderInlineEnd: 'none', paddingTop: 8 }}
        />
      </Sider>

      <Layout>
        <Header className="gew-header">
          <div className="gew-row" style={{ gap: 12 }}>
            {!isMobile ? (
              <Button
                type="text"
                aria-label={collapsed ? '展开导航' : '收起导航'}
                icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={toggleSidebar}
              />
            ) : null}
            <span className="gew-header__title">{activeItem?.label ?? '工 e 稳袋'}</span>
          </div>

          <div className="gew-header__meta">
            <span className="gew-hide-sm">{user?.merchant?.business_name ?? ''}</span>
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'settings',
                    icon: <UserOutlined />,
                    label: '我的',
                    onClick: () => navigate('/settings'),
                  },
                  {
                    key: 'logout',
                    icon: <LogoutOutlined />,
                    label: '退出登录',
                    danger: true,
                    onClick: () => logoutMutation.mutate(),
                  },
                ],
              }}
            >
              <button
                type="button"
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  padding: 4,
                }}
              >
                <Avatar size={26} style={{ background: '#F2F3F5', color: '#666' }}>
                  {user?.display_name?.slice(0, 1) ?? '用'}
                </Avatar>
                <span style={{ fontSize: 13 }}>
                  {user?.display_name}
                  {role ? (
                    <span style={{ color: 'var(--text-muted)', marginLeft: 6 }}>
                      {ROLE_LABELS[role]}
                    </span>
                  ) : null}
                </span>
              </button>
            </Dropdown>
          </div>
        </Header>

        <Content>
          <div className="gew-content">
            <Outlet />
          </div>
        </Content>
      </Layout>

      <nav className="gew-tabbar" aria-label="主导航">
        {navItems
          .filter((item) => item.mobile)
          .map((item) => (
            <Tooltip key={item.key} title={item.label} placement="top">
              <button
                type="button"
                className={`gew-tabbar__item${item.key === selectedKey ? ' gew-tabbar__item--active' : ''}`}
                aria-current={item.key === selectedKey ? 'page' : undefined}
                onClick={() => navigate(item.path)}
              >
                <span className="gew-tabbar__icon" aria-hidden="true">
                  {item.icon}
                </span>
                {item.label}
              </button>
            </Tooltip>
          ))}
      </nav>
    </Layout>
  );
}
