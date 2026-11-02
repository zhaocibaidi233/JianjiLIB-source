import { createRouter, createWebHistory } from 'vue-router'
import { auth, can, ensureAuthReady } from './lib/auth'
import { readerAuth, ensureReaderReady } from './lib/readerAuth'

const AdminShell = () => import('./admin/AdminShell.vue')
const Dashboard = () => import('./admin/views/Dashboard.vue')
const Circulation = () => import('./admin/views/Circulation.vue')
const Readers = () => import('./admin/views/Readers.vue')
const ReaderDetail = () => import('./admin/views/ReaderDetail.vue')
const RecordDetail = () => import('./admin/views/RecordDetail.vue')
const LegacyRecordList = () => import('./legacy/views/RecordList.vue')
const LegacyRecordEdit = () => import('./legacy/views/RecordEdit.vue')
const LegacyBarcodes = () => import('./legacy/views/Barcodes.vue')
const Labels = () => import('./admin/views/Labels.vue')
const Inventory = () => import('./admin/views/Inventory.vue')
const InventorySession = () => import('./admin/views/InventorySession.vue')
const Inspect = () => import('./admin/views/Inspect.vue')
const Dictionaries = () => import('./admin/views/Dictionaries.vue')
const Property = () => import('./admin/views/Property.vue')
const Accession = () => import('./admin/views/Accession.vue')
const Resources = () => import('./admin/views/Resources.vue')
const ImportExport = () => import('./admin/views/ImportExport.vue')
const Logs = () => import('./admin/views/Logs.vue')
const Reservations = () => import('./admin/views/Reservations.vue')
const Recommendations = () => import('./admin/views/Recommendations.vue')
const Acquisition = () => import('./admin/views/Acquisition.vue')
const Stats = () => import('./admin/views/Stats.vue')
const Settings = () => import('./admin/views/Settings.vue')
const Users = () => import('./admin/views/Users.vue')
const Account = () => import('./admin/views/Account.vue')
const NoAccess = () => import('./admin/views/NoAccess.vue')
const ReaderShell = () => import('./reader/ReaderShell.vue')
const ReaderDiscovery = () => import('./reader/Discovery.vue')
const ReaderCatalog = () => import('./reader/Catalog.vue')
const ReaderLoans = () => import('./reader/MyLoans.vue')
const ReaderSeats = () => import('./reader/Seats.vue')
const ReaderAccount = () => import('./reader/Account.vue')

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/admin' },
    {
      path: '/login',
      component: () => import('./views/Login.vue'),
      meta: { title: '登录' },
    },
    {
      path: '/admin',
      component: AdminShell,
      children: [
        {
          path: '',
          component: Dashboard,
          meta: { title: '工作台', subtitle: '今天的流通、待办与馆藏概况' },
        },
        {
          path: 'circulation',
          component: Circulation,
          meta: {
            title: '借还台',
            subtitle: '外借、归还、续借与逾期处理',
            module: 'circulation',
          },
        },
        {
          path: 'inventory',
          component: Inventory,
          meta: {
            title: '盘点',
            subtitle: '扫码对账，只出清单不改馆藏',
            module: 'circulation',
          },
        },
        {
          path: 'inventory/:id',
          component: InventorySession,
          meta: {
            title: '盘点明细',
            subtitle: '扫描进度、漏盘与外借未归清单',
            module: 'circulation',
          },
        },
        {
          path: 'readers',
          component: Readers,
          meta: {
            title: '读者',
            subtitle: '读者档案、状态与借阅历史',
            module: 'readers',
          },
        },
        {
          path: 'readers/:id',
          component: ReaderDetail,
          meta: {
            title: '读者档案',
            subtitle: '外借、历史与费用记录',
            module: 'readers',
          },
        },
        {
          path: 'reservations',
          component: Reservations,
          meta: {
            title: '预约到书',
            subtitle: '预约队列、到书通知与保留期',
            module: 'circulation',
          },
        },
        {
          path: 'recommendations',
          component: Recommendations,
          meta: {
            title: '图书推荐',
            subtitle: '馆员精选、热门借阅与新到图书',
            module: 'circulation',
            wide: true,
          },
        },
        {
          path: 'acquisitions',
          component: Acquisition,
          meta: {
            title: '荐购与受赠',
            subtitle: '来源登记、审议确认与备案',
            module: 'circulation',
          },
        },
        {
          path: 'records',
          component: LegacyRecordList,
          meta: {
            title: '图书库',
            subtitle: '编目成品库与馆藏记录',
            module: 'records',
            legacy: true,
          },
        },
        {
          path: 'records/new',
          component: LegacyRecordEdit,
          meta: {
            title: '新增图书',
            subtitle: '套录、著录、质检与馆藏条码',
            module: 'records',
            legacy: true,
            write: true,
          },
        },
        {
          path: 'records/temp',
          component: LegacyRecordEdit,
          meta: {
            title: '套录底稿',
            subtitle: '校对套录字段后入库',
            module: 'records',
            legacy: true,
            write: true,
          },
        },
        {
          path: 'records/:id/edit',
          component: LegacyRecordEdit,
          meta: {
            title: '编辑书目',
            subtitle: '修改著录字段、分类与馆藏',
            module: 'records',
            legacy: true,
            write: true,
          },
        },
        {
          path: 'records/:id',
          component: RecordDetail,
          meta: {
            title: '书目详情',
            subtitle: '馆藏、MARC 字段与质检结果',
            module: 'records',
          },
        },
        {
          path: 'resources',
          component: Resources,
          meta: {
            title: '资源库',
            subtitle: '套录原料库与书商数据',
            module: 'resources',
          },
        },
        {
          path: 'io',
          component: ImportExport,
          meta: {
            title: '导入导出',
            subtitle: '书目文件、Excel 与后台任务',
            module: 'io',
          },
        },
        {
          path: 'barcodes',
          component: LegacyBarcodes,
          meta: {
            title: '条码管理',
            subtitle: '全馆条码、注销与恢复',
            module: 'barcodes',
            legacy: true,
          },
        },
        {
          path: 'labels',
          component: Labels,
          meta: {
            title: '书标打印',
            subtitle: '书标预览、版式设置与批量打印',
            module: 'labels',
            legacy: true,
          },
        },
        {
          path: 'inspect',
          component: Inspect,
          meta: {
            title: '数据体检',
            subtitle: '全库质量问题扫描与定位',
            module: 'inspect',
          },
        },
        {
          path: 'dictionaries',
          component: Dictionaries,
          meta: {
            title: '字典中心',
            subtitle: '馆藏地点、语种与著录候选',
            module: 'dictionaries',
          },
        },
        {
          path: 'logs',
          component: Logs,
          meta: {
            title: '操作日志',
            subtitle: '书目变更记录与责任人',
            module: 'records',
          },
        },
        {
          path: 'stats',
          component: Stats,
          meta: {
            title: '统计分析',
            subtitle: '在架口径总览、五个维度与编目工作量',
            module: 'stats',
          },
        },
        {
          path: 'property',
          component: Property,
          meta: {
            title: '财产登记',
            subtitle: '按册出账的进书清册',
            module: 'stats',
          },
        },
        {
          path: 'accession',
          component: Accession,
          meta: {
            title: '登录账',
            subtitle: '总括登录与个别登录，资产台账口径',
            module: 'stats',
          },
        },
        {
          path: 'settings',
          component: Settings,
          meta: {
            title: '全局设置',
            subtitle: '图书库、资源库、编目规则与数据工具',
            module: 'settings',
            write: true,
          },
        },
        {
          path: 'users',
          component: Users,
          meta: {
            title: '用户与权限',
            subtitle: '账号、角色权限、注册申请与接口凭据',
            module: 'users',
            write: true,
          },
        },
        {
          path: 'account',
          component: Account,
          meta: {
            title: '我的账号',
            subtitle: '个人信息、密码与登录状态',
          },
        },
        {
          path: 'no-access',
          component: NoAccess,
          meta: { title: '暂无访问权限' },
        },
      ],
    },
    {
      path: '/reader',
      component: ReaderShell,
      children: [
        { path: '', component: ReaderDiscovery, meta: { title: '发现' } },
        { path: 'catalog', component: ReaderCatalog, meta: { title: '馆藏检索' } },
        { path: 'loans', component: ReaderLoans, meta: { title: '我的借阅' } },
        { path: 'seats', component: ReaderSeats, meta: { title: '座位预约' } },
        { path: 'account', component: ReaderAccount, meta: { title: '我的账号' } },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/admin' },
  ],
})

router.beforeEach(async (to) => {
  if (to.path === '/reader' || to.path.startsWith('/reader/')) {
    await ensureReaderReady()
    if (!readerAuth.authenticated) {
      return {
        path: '/login',
        query: { portal: 'reader', redirect: to.fullPath === '/reader' ? undefined : to.fullPath },
      }
    }
    return true
  }

  await ensureAuthReady()

  if (!auth.enabled) return true

  if (!auth.authenticated) {
    if (to.path === '/login') return true
    return { path: '/login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }

  if (to.path === '/login') {
    // 馆员已登录时仍允许打开读者登录页，两套会话互不影响
    if (to.query.portal === 'reader') return true
    return '/admin'
  }
  if (to.path === '/admin/no-access') return true
  // read 页对「可读」放行；声明了 `write: true` 的页面（编目写入、全局设置、
  // 用户管理）要求 write 级——只读角色进这些页本来就什么都保存不了，早一步
  // 拦在门外比进页面后处处按钮置灰清楚（2026-10-25 审查 P2-1）。
  // 混合页（借还台、读者档案等）保持 read 级放行，写操作由各视图的按钮级
  // `can(module, 'write')` 控制。
  if (!can(to.meta.module, to.meta.write ? 'write' : 'read')) return '/admin/no-access'
  return true
})

router.afterEach((to) => {
  const title = to.meta.title || '简籍'
  document.title = `${title} · 简籍 JIANJI`
})

// —— 被动 401 的跳转 ——
// 拦截器（lib/api.ts）只负责清令牌、发事件；「送到哪个页面」只有路由知道。
// 此前监听器只清状态（lib/auth.ts / lib/readerAuth.ts），会话过期后用户停在
// 原页只看到一条 toast——现在由这里把人送回登录页并带上 redirect，登录后
// 原路返回（2026-10-25 审查 P1-5；legacy/api.ts 顶部注释描述的
// 「弹回登录页」自本次起才是真实行为）。
window.addEventListener('jianji:unauthorized', () => {
  const current = router.currentRoute.value
  if (current.path === '/login') return
  void router.push({
    path: '/login',
    query: current.fullPath === '/' ? {} : { redirect: current.fullPath },
  })
})

window.addEventListener('jianji:reader-unauthorized', () => {
  const current = router.currentRoute.value
  if (current.path.startsWith('/reader')) {
    void router.push({
      path: '/login',
      query: { portal: 'reader', redirect: current.fullPath },
    })
  }
})
