/**
 * 统一响应式抽屉 / 移动端底部工具栏。
 *
 * 桌面使用设计宽度（480–860px），手机一律全屏（100vw）并在底部固定操作区，
 * 避免「按钮被底部导航遮住」或「抽屉超宽点不到」这类问题。
 *
 * 现有的 5 个抽屉继续使用 `useDrawerWidth()`；需要底部固定操作区时改用
 * `ResponsiveDrawer`，让保存 / 提交类按钮在手机上始终可点且高度 >= 44px。
 */

import type { ReactNode } from 'react';
import { Drawer } from 'antd';
import type { DrawerProps } from 'antd';

import { useIsMobile } from '@/hooks/useResponsive';

export interface ResponsiveDrawerProps extends Omit<DrawerProps, 'width'> {
  /** 桌面宽度；手机端忽略此值，一律 100vw */
  desktopWidth?: number;
  /** 固定在底部的操作区（保存 / 提交 / 确认分享等） */
  footer?: ReactNode;
}

export default function ResponsiveDrawer({
  desktopWidth = 560,
  footer,
  children,
  ...rest
}: ResponsiveDrawerProps) {
  const isMobile = useIsMobile();
  return (
    <Drawer
      {...rest}
      width={isMobile ? '100%' : desktopWidth}
      className={`gew-drawer${isMobile ? ' gew-drawer--mobile' : ''}`}
      footer={footer ? <div className="gew-drawer__footer">{footer}</div> : undefined}
    >
      {children}
    </Drawer>
  );
}
