export interface NavItem {
  to: string
  label: string
  icon: string
  module?: string
  badge?: 'overdue'
  description?: string
}

export type Board = 'catalog' | 'circulation'

export interface NavGroup {
  label: string
  items: NavItem[]
  /** shared 组（总览 / 系统）在两个工作板块下都显示 */
  board: Board | 'shared'
}

export const BOARD_ORDER: Array<{ key: Board; label: string; icon: string }> = [
  { key: 'circulation', label: '借阅', icon: 'swap' },
  { key: 'catalog', label: '编目', icon: 'book-open' },
]

export const NAV_GROUPS: NavGroup[] = [
  {
    label: '总览',
    board: 'shared',
    items: [
      { to: '/admin', label: '工作台', icon: 'dashboard', description: '今日流通概览与待办' },
    ],
  },
  {
    label: '流通服务',
    board: 'circulation',
    items: [
      { to: '/admin/circulation', label: '借还台', icon: 'swap', module: 'circulation', badge: 'overdue', description: '借书、归还、续借与借阅记录' },
      { to: '/admin/inventory', label: '盘点', icon: 'list', module: 'circulation', description: '扫码对账，只出清单不改馆藏' },
      { to: '/admin/readers', label: '读者', icon: 'users', module: 'readers', description: '读者档案、状态与借阅历史' },
      { to: '/admin/reservations', label: '预约到书', icon: 'bookmark', module: 'circulation', description: '读者预约排队与到书通知' },
      { to: '/admin/recommendations', label: '图书推荐', icon: 'star', module: 'circulation', description: '馆员精选、热门借阅与新到图书' },
      { to: '/admin/acquisitions', label: '荐购与受赠', icon: 'file', module: 'circulation', description: '来源登记、审议确认与备案' },
    ],
  },
  {
    label: '馆藏资源',
    board: 'catalog',
    items: [
      { to: '/admin/records', label: '图书库', icon: 'books', module: 'records', description: '编目成品库与馆藏记录' },
      { to: '/admin/barcodes', label: '条码管理', icon: 'barcode', module: 'barcodes', description: '全馆条码、注销与恢复' },
      { to: '/admin/labels', label: '书标打印', icon: 'printer', module: 'labels', description: 'Code39 书标、格式与批量打印' },
      { to: '/admin/inspect', label: '数据体检', icon: 'shield', module: 'inspect', description: '全库质量问题扫描' },
    ],
  },
  {
    label: '数据工具',
    board: 'catalog',
    items: [
      { to: '/admin/resources', label: '资源库', icon: 'database', module: 'resources', description: '套录数据源与书商数据' },
      { to: '/admin/io', label: '导入导出', icon: 'swap', module: 'io', description: '书目文件、Excel 与批量套录' },
    ],
  },
  {
    label: '统计',
    board: 'catalog',
    items: [
      { to: '/admin/stats', label: '统计分析', icon: 'chart', module: 'stats', description: '馆藏、流通与编目工作量' },
      { to: '/admin/property', label: '财产登记', icon: 'receipt', module: 'stats', description: '按册出账的进书清册' },
      { to: '/admin/accession', label: '登录账', icon: 'archive', module: 'stats', description: '总括登录与个别登录，资产台账口径' },
    ],
  },
  {
    label: '系统',
    board: 'shared',
    items: [
      { to: '/admin/settings', label: '全局设置', icon: 'settings', module: 'settings', description: '图书库、套录源与流通规则' },
      { to: '/admin/users', label: '用户与权限', icon: 'shield', module: 'users', description: '账号、角色与接口凭据' },
    ],
  },
]

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items)

export function navForPath(path: string): NavItem | undefined {
  return NAV_ITEMS.find((item) =>
    item.to === '/admin' ? path === '/admin' : path.startsWith(item.to),
  )
}


const CIRCULATION_PREFIXES = [
  '/admin/circulation', '/admin/inventory', '/admin/readers',
  '/admin/reservations', '/admin/recommendations', '/admin/acquisitions',
] as const
const CATALOG_PREFIXES = [
  '/admin/records', '/admin/resources', '/admin/io', '/admin/barcodes',
  '/admin/labels', '/admin/inspect',
  '/admin/dictionaries', '/admin/logs', '/admin/stats', '/admin/property',
  '/admin/accession',
] as const

function underPath(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(prefix + '/')
}

/** 路径属于哪个板块；工作台 / 设置 / 账号等共享页返回 null。 */
export function boardOfPath(path: string): Board | null {
  if (CIRCULATION_PREFIXES.some((prefix) => underPath(path, prefix))) {
    return 'circulation'
  }
  if (CATALOG_PREFIXES.some((prefix) => underPath(path, prefix))) return 'catalog'
  return null
}

/** 切换板块时的落点：第一个当前账号有读权限的页面。 */
export function boardHome(
  board: Board,
  allowed: (module: string) => boolean,
): string {
  const items = board === 'catalog'
    ? [
        ['/admin/records', 'records'], ['/admin/barcodes', 'barcodes'],
        ['/admin/labels', 'labels'], ['/admin/resources', 'resources'],
        ['/admin/io', 'io'], ['/admin/inspect', 'inspect'],
        ['/admin/dictionaries', 'dictionaries'],
        ['/admin/stats', 'stats'], ['/admin/property', 'stats'],
        ['/admin/accession', 'stats'],
      ]
    : [
        ['/admin/circulation', 'circulation'], ['/admin/readers', 'readers'],
        ['/admin/inventory', 'circulation'], ['/admin/reservations', 'circulation'],
        ['/admin/recommendations', 'circulation'],
        ['/admin/acquisitions', 'circulation'],
      ]
  const hit = items.find(([, module]) => allowed(module))
  return hit?.[0] || '/admin'
}
