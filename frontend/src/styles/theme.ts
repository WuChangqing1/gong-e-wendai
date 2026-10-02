/**
 * Ant Design 主题定制。
 *
 * 目标：不保留任何 Ant Design 默认蓝色体系，统一为品牌红 + 中性灰。
 */

import type { ThemeConfig } from 'antd';

export const BRAND = {
  red: '#D90000',
  redHover: '#B50000',
  redActive: '#990000',
  redSoft: '#FFF1F1',
  redBorder: '#F4CACA',
  pageBg: '#F6F7F9',
  surface: '#FFFFFF',
  textPrimary: '#1F1F1F',
  textSecondary: '#666666',
  textMuted: '#999999',
  border: '#E8E8E8',
  divider: '#F0F0F0',
  success: '#178A4B',
  warning: '#D97706',
  danger: '#C62828',
  info: '#3568A8',
} as const;

export const FONT_FAMILY =
  '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", "Hiragino Sans GB", Arial, sans-serif';

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: BRAND.red,
    colorPrimaryHover: BRAND.redHover,
    colorPrimaryActive: BRAND.redActive,
    colorPrimaryBg: BRAND.redSoft,
    colorPrimaryBgHover: BRAND.redSoft,
    colorPrimaryBorder: BRAND.redBorder,
    colorLink: BRAND.red,
    colorLinkHover: BRAND.redHover,
    colorLinkActive: BRAND.redActive,

    colorSuccess: BRAND.success,
    colorWarning: BRAND.warning,
    colorError: BRAND.danger,
    colorInfo: BRAND.info,

    colorText: BRAND.textPrimary,
    colorTextSecondary: BRAND.textSecondary,
    colorTextTertiary: BRAND.textMuted,
    colorTextQuaternary: '#BFBFBF',

    colorBgLayout: BRAND.pageBg,
    colorBgContainer: BRAND.surface,
    colorBorder: BRAND.border,
    colorBorderSecondary: BRAND.divider,
    colorSplit: BRAND.divider,

    fontFamily: FONT_FAMILY,
    fontSize: 14,
    borderRadius: 8,
    borderRadiusLG: 12,
    borderRadiusSM: 4,
    controlHeight: 36,
    lineWidth: 1,
    wireframe: false,
    boxShadow: '0 1px 2px rgba(15, 23, 42, 0.04)',
    boxShadowSecondary: '0 2px 8px rgba(15, 23, 42, 0.06)',
  },
  components: {
    Layout: {
      headerBg: BRAND.surface,
      headerHeight: 56,
      headerPadding: '0 24px',
      bodyBg: BRAND.pageBg,
      siderBg: BRAND.surface,
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: BRAND.redSoft,
      itemSelectedColor: BRAND.red,
      itemHoverBg: '#F5F6F8',
      itemHoverColor: BRAND.textPrimary,
      itemColor: BRAND.textSecondary,
      itemHeight: 40,
      itemMarginInline: 8,
      itemBorderRadius: 8,
      activeBarWidth: 0,
      iconSize: 16,
    },
    Card: {
      borderRadiusLG: 12,
      paddingLG: 20,
      colorBorderSecondary: BRAND.border,
    },
    Button: {
      controlHeight: 36,
      fontWeight: 500,
      primaryShadow: 'none',
      defaultShadow: 'none',
      dangerShadow: 'none',
      borderRadius: 8,
    },
    Table: {
      headerBg: '#FAFBFC',
      headerColor: BRAND.textSecondary,
      headerSplitColor: 'transparent',
      borderColor: BRAND.divider,
      rowHoverBg: '#FAFBFC',
      cellPaddingBlock: 12,
      cellPaddingInline: 16,
    },
    Tabs: {
      inkBarColor: BRAND.red,
      itemSelectedColor: BRAND.red,
      itemHoverColor: BRAND.redHover,
      itemColor: BRAND.textSecondary,
      titleFontSize: 14,
    },
    Tag: {
      defaultBg: '#F5F6F8',
      defaultColor: BRAND.textSecondary,
      borderRadiusSM: 4,
    },
    Statistic: {
      contentFontSize: 24,
      titleFontSize: 13,
    },
    Descriptions: {
      labelBg: '#FAFBFC',
      titleMarginBottom: 12,
    },
    Steps: {
      colorPrimary: BRAND.red,
    },
    Alert: {
      borderRadiusLG: 8,
    },
    Modal: {
      borderRadiusLG: 12,
      titleFontSize: 16,
    },
    Drawer: {
      paddingLG: 20,
    },
    Input: {
      controlHeight: 36,
      activeShadow: `0 0 0 2px ${BRAND.redSoft}`,
    },
    Select: {
      controlHeight: 36,
      optionSelectedBg: BRAND.redSoft,
    },
    DatePicker: {
      controlHeight: 36,
      cellActiveWithRangeBg: BRAND.redSoft,
    },
    Segmented: {
      itemSelectedBg: BRAND.surface,
      itemSelectedColor: BRAND.red,
      trackBg: '#F2F3F5',
    },
    Progress: {
      defaultColor: BRAND.red,
    },
  },
};
