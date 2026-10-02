/** 产品标识与页头。 */

import { BRAND } from '@/styles/theme';

export function BrandMark({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <div className="gew-brand" aria-label="工 e 稳袋">
      <span className="gew-brand__mark" aria-hidden="true">
        工
      </span>
      {!collapsed && (
        <span>
          <div className="gew-brand__text">工 e 稳袋</div>
          <div className="gew-brand__sub">经营资金 · 家庭协同</div>
        </span>
      )}
    </div>
  );
}

export function ProductLogo({ size = 32 }: { size?: number }) {
  return (
    <span
      aria-hidden="true"
      style={{
        width: size,
        height: size,
        borderRadius: size * 0.22,
        background: BRAND.red,
        color: '#fff',
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontWeight: 700,
        fontSize: size * 0.52,
        flex: '0 0 auto',
      }}
    >
      工
    </span>
  );
}
