/**
 * 手机端「我的」设置菜单。
 *
 * 桌面端用 Tabs 平铺五个设置分组；手机上横向页签既不便于点按，也看不出
 * 每组里有什么。这里改为纵向列表：每项一行，带说明与进入箭头，
 * 点进去只显示该分组内容，顶部提供返回。
 */

import { Button, List } from 'antd';
import { LeftOutlined, RightOutlined } from '@ant-design/icons';
import type { ReactNode } from 'react';

export interface SettingsSection {
  key: string;
  label: string;
  description: string;
  icon: ReactNode;
  content: ReactNode;
}

export function MobileSettingsMenu({
  sections,
  onOpen,
}: {
  sections: SettingsSection[];
  onOpen: (key: string) => void;
}) {
  return (
    <List
      className="gew-settings-menu"
      data-testid="settings-menu"
      itemLayout="horizontal"
      dataSource={sections}
      renderItem={(item) => (
        <List.Item
          key={item.key}
          onClick={() => onOpen(item.key)}
          className="gew-settings-menu__item"
          extra={<RightOutlined style={{ color: 'var(--text-muted)' }} />}
        >
          <List.Item.Meta
            avatar={<span className="gew-settings-menu__icon">{item.icon}</span>}
            title={item.label}
            description={item.description}
          />
        </List.Item>
      )}
    />
  );
}

export function MobileSettingsDetail({
  section,
  onBack,
}: {
  section: SettingsSection;
  onBack: () => void;
}) {
  return (
    <div className="gew-stack" data-testid="settings-detail">
      <div className="gew-settings-detail__bar">
        <Button type="text" icon={<LeftOutlined />} onClick={onBack} aria-label="返回设置列表">
          返回
        </Button>
        <span className="gew-settings-detail__title">{section.label}</span>
      </div>
      {section.content}
    </div>
  );
}
