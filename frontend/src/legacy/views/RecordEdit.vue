<template>
  <el-card shadow="never" v-loading="loading" class="editor-card legacy-page legacy-editor-page">
    <!-- 顶部工具条 + 质检提示条整块已搬进 components/EditorToolbar.vue（块 A）。
         两个动作的副作用留在页面：返回走路由；保存要过质检拦截、条码校验与强制入库对话框。 -->
    <EditorToolbar
      :is-new="isNew" :is-temp="isTemp" :id="id" :detail="detail"
      :hidden-fields="hiddenFields" :findings="findings" :has-error="hasError"
      :quality-summary="qualitySummary" :saving="saving"
      @back="router.push('/admin/records')" @save="saveAndHoldings()" />

    <el-row :gutter="12" class="editor-row">
      <el-col :span="17">
        <el-tabs v-model="tab">
          <!-- ===== 简编表单 ===== -->
          <el-tab-pane label="简编表单" name="simple">
            <!-- 版面目标：**不滚动就能看全**。三条实现要点：
                 ① 短字段给 span 6/8，与同排字段拼满一行（每组占宽之和是 24 的倍数，有单测守着）；
                 ② 当前字段都没给框内 placeholder，说明走 `title` 悬停 —— 但**这不再是硬规矩**
                    （"不留框内灰字"那条约定 2026-10-06 已作废，见项目记忆 §5）；
                 ③ 候选 chip 只在字段为空或聚焦时出现，避免把窄栏撑成多行。 -->
            <el-form label-width="86px" class="simple-form" size="default" @submit.prevent
              @keydown="onSimpleKeydown">
              <template v-for="g in GROUPS" :key="g.key">
                <!-- 组「记录级信息」已撤：卷册号/排架号搬进「分类与主题」内联（§5.1）。
                     防御性写法——万一以后有人把 record 加回 GROUPS，也不会多出一个空标题。 -->
                <template v-if="g.key !== 'record'">
                <div class="group-head" :class="{ 'is-optional': g.key === 'series' || g.key === 'note' }">
                  <span class="group-bar" :class="{ 'is-optional': g.key === 'series' || g.key === 'note' }" />
                  <span class="group-title">{{ g.label }}</span>
                  <span v-if="g.key === 'title'" class="group-legend">
                    带 <b class="req-star">*</b> 为必备
                  </span>
                  <span v-else-if="g.key === 'series' || g.key === 'note'" class="group-optional">可选</span>
                </div>

                <el-row :gutter="12">
                  <!-- `data-simple-key` 是简编表单键盘导航的锚点（`onSimpleKeydown` 靠它认字段）：
                       放在 el-col 上，输入框在它内部，`closest()` 一定能找到。
                       导航顺序/行结构见 `simpleForm.ts::simpleFormRows()`（唯一真源）。
                       ⚠️ 组6（分类与主题）**不走**这个通用分支：它是整块自定义的
                       「分类号 / 书次号 / 卷册号」一行（见下方 `v-else`）。 -->
                  <template v-if="g.key !== 'subject'">
                  <el-col v-for="b in bindingsInGroup(g.key)" :key="b.tag + b.code"
                    :span="colSpan(b)"
                    :class="{ 'field-optional': isOptional(b) }"
                    :data-simple-key="b.tag + b.code">
                    <!-- 标签只用中文名：简编页面向的是**编目员**，不是看 MARC 的人。
                         字段号（200$a）已按用户要求撤掉 —— 需要看字段号就用「全字段 MARC」tab。
                         `#label` 插槽让 b.label 仍是纯中文名，便于复用与测试。 -->
                    <el-form-item>
                      <template #label>
                        <span v-if="b.required" class="req-star" title="必备字段（简要级次要求，缺失不能入库）">*</span>{{ b.label }}
                      </template>
                      <template v-if="b.type === 'textarea'">
                        <el-input v-model="simple[b.tag + b.code]" type="textarea" :rows="2" />
                      </template>
                      <!-- 语种：值是 ISO 639 三字母码（chi/eng…），编目员背不下来，用下拉选。
                           下拉里显示「中文名（码）」，存进 MARC 的是码。 -->
                      <el-select v-else-if="b.suggest === 'select-language'"
                        v-model="simple[b.tag + b.code]" filterable allow-create clearable
                        placeholder=""
                        style="width: 100%" @focus="focusedKey = b.tag + b.code"
                        @blur="onSimpleSelectBlur(b, $event)" @change="onLanguageChange"
                        title="下拉显示「中文名（三字母码）」，保存的是语种代码（如 chi）">
                        <el-option v-for="l in languages" :key="l.code"
                          :label="`${l.name}（${l.code}）`" :value="l.code" />
                      </el-select>
                      <!-- 装订等带 options 的字段：CNMARC 010$b 本质自由文本，故 allow-create
                           允许手打枚举外的值；刻意**不加** default-first-option —— 项目踩过
                           "回车被下拉吃掉、悄悄换掉刚敲的值"的坑（见 suggest 字段注释）。
                           `@blur` 是必需的补丁：**打了字没点候选就失焦，Element 会把文字丢掉**
                           （见 `onSimpleSelectBlur`）。 -->
                      <el-select v-else-if="b.options" v-model="simple[b.tag + b.code]"
                        filterable allow-create clearable placeholder=""
                        style="width: 100%" @blur="onSimpleSelectBlur(b, $event)">
                        <el-option v-for="o in b.options" :key="o" :label="o" :value="o" />
                      </el-select>
                      <el-input v-else-if="b.unit && b.unitPos === 'prepend'"
                        v-model="simple[b.tag + b.code]" :class="numClass(b)"
                        @focus="focusedKey = b.tag + b.code" @blur="focusedKey = ''">
                        <!-- 单位走 displayUnit()：定价的前缀是**本馆默认币种**（可配），
                             其余字段（页/cm）是固定单位。 -->
                        <template #prepend><span class="unit-fixed">{{ displayUnit(b) }}</span></template>
                      </el-input>
                      <el-input v-else-if="b.unit && b.unitPos === 'append'"
                        v-model="simple[b.tag + b.code]" :class="numClass(b)"
                        @focus="focusedKey = b.tag + b.code" @blur="focusedKey = ''">
                        <template #append><span class="unit-fixed">{{ b.unit }}</span></template>
                      </el-input>
                      <el-input v-else v-model="simple[b.tag + b.code]" :class="numClass(b)"
                        @focus="focusedKey = b.tag + b.code"
                        @blur="onSimpleBlur(b); focusedKey = ''" />
                      <!-- 候选做成**输入框下方的一排可点标签**，不是下拉：
                           下拉会把回车变成"选第一条"，把刚敲的值悄悄换掉（分类号踩过这个坑）。
                           只在**聚焦时**出现，而且是**浮层**（`.suggest-pop`）——
                           块级渲染会在窄栏里折成 3 行（实测 62px），把下面的字段整体推下去、
                           连带触发滚动。浮层不参与布局，聚焦前后表单高度完全不变。
                           `@mousedown.prevent` 是必需的：否则点 chip 会先让输入框失焦、
                           候选行随即消失，click 事件根本到不了（这是 focus-only 的经典坑）。
                           点击行为分两种（`onSuggestClick`）：一般候选是**替换**，
                           责任方式是**追加**（`200$f` 是「姓名 + 责任方式」连写）。 -->
                      <div v-if="showSuggest(b)" class="suggest-pop"
                        :class="{ 'suggest-pop--scroll': b.suggest === 'responsibility' }">
                        <span v-for="v in suggestionsFor(b)" :key="v" class="suggest-chip"
                          @mousedown.prevent @click="onSuggestClick(b, v)">{{ v }}</span>
                      </div>
                    </el-form-item>
                  </el-col>
                  </template>

                  <!-- ===== 组6「分类与主题」：整块自定义 =====
                       一行读作**索书号**：分类号 / 书次号 / 卷册号（与录入栏同构 ——
                       用户："书次号、卷册号用灰色预输入提示显示在框里就行，参考录入栏，
                       不要放外面占位置"）。
                       三格装进**同一个 el-col**、共用**一个**外置标签（分类号）；
                       书次号 / 卷册号只靠**框内灰色占位**说明自己是谁 —— 标签宽 86px，
                       三格各带一个就是 258px 白占宽度，窄栏里的框会被挤成一条缝。
                       书次号 / 卷册号 / 排架号都是**记录级**列（不是 CNMARC 子字段），
                       绝不进 BINDINGS（README 规则 12）；栅格配比走 `subjectRowSpans()` 唯一真源。
                       书次号绑的是**录入栏同一份 `extra.bookNo`** → 天然双向同步（规则 13）。 -->
                  <template v-else>
                  <el-col :span="subjectRowSpans(shelfNoEnabled).callNo">
                    <el-form-item>
                      <template #label>
                        <span v-if="classNoBinding.required" class="req-star"
                          title="必备字段（简要级次要求，缺失不能入库）">*</span>索书号
                      </template>
                      <div class="callno-panel">
                        <div class="callno-row">
                          <el-input v-model="simple['690a']" class="callno-cls"
                            data-simple-key="690a" placeholder="分类号" aria-label="分类号"
                            @focus="focusedKey = '690a'" @blur="focusedKey = ''" />
                          <span class="callno-sep">/</span>
                          <el-input v-model="extra.bookNo" class="callno-no optional-control"
                            data-simple-key="bookNo" placeholder="书次号" aria-label="书次号"
                            @input="bookNoTouched = true" />
                          <!-- 卷册号：**可手输 + 可下拉 + 可清空**（filterable allow-create clearable）。
                               必须带 `el-option` children —— 曾在并入分类号行时误删，导致下拉为空、
                               只能手打（用户反馈"恢复之前的样式"）。与录入栏同源 `VOLUME_OPTIONS`。 -->
                          <el-select v-model="extra.volume" class="callno-vol optional-control"
                            data-simple-key="volume" filterable allow-create clearable
                            placeholder="卷册号" @blur="commitVolumeTyped">
                            <el-option v-for="o in VOLUME_OPTIONS" :key="o.value"
                              :label="o.value" :value="o.value" />
                          </el-select>
                        </div>
                        <div
                          class="callno-preview"
                          :class="{ 'is-empty': !callNoPreview, 'is-incomplete': callNoIncomplete }"
                          :title="callNoPreview ? (callNoIncomplete ? '分类号未填' : '随输入实时更新') : ''"
                          aria-live="polite"
                          aria-atomic="true"
                        >
                          <template v-if="callNoPreview">
                            <span class="callno-preview-dot" aria-hidden="true"></span>
                            <strong>{{ callNoPreview }}</strong>
                          </template>
                          <span v-else class="callno-skeleton-group" aria-hidden="true">
                            <span class="callno-skeleton is-class"></span>
                            <span class="callno-skeleton-sep">/</span>
                            <span class="callno-skeleton is-book"></span>
                            <span class="callno-skeleton-sep">:</span>
                            <span class="callno-skeleton is-volume"></span>
                          </span>
                        </div>
                      </div>
                    </el-form-item>
                  </el-col>
                  </template>

                  <!-- 排架号同属索书号这一行，**也不放外置标签**（`label-width="0"`）：
                       它的栅格只有 4 格，86px 的标签会把输入框压到 ~70px。
                       去掉标签后实际输入宽度（≈158px）比原来 6 格带标签（≈152px）还宽一点，
                       所以这不是牺牲，只是把标签挪进框里当灰字提示。 -->
                  <el-col v-if="g.key === 'subject' && shelfNoEnabled"
                    :span="subjectRowSpans(shelfNoEnabled).shelfNo"
                    class="field-optional" data-simple-key="shelfNo">
                    <el-form-item label-width="0">
                      <el-input v-model="extra.shelfNo" placeholder="排架号" :title="SHELF_NO_TITLE" />
                      <div v-if="shelfNoWarning" class="suggest-line">{{ shelfNoWarning }}</div>
                    </el-form-item>
                  </el-col>

                  <!-- 「分类与主题」的右半栏：**只读**检索点。
                       606 主题词 / 7xx 责任者是**多值检索点**，简编表单每栏只有一个输入框，
                       做成可编辑会变成"只改到第一条"的隐蔽陷阱 → 只读展示 + 跳转全字段。
                       放在分类号**同一行的剩余栅格**里（GROUP_EXTRA_SPAN），不再各占一整块；
                       空态仍渲染（写"无"）：区块的意义就是让编目员知道有这些字段。 -->
                  <el-col v-if="GROUP_EXTRA_SPAN[g.key]"
                    :span="g.key === 'subject'
                      ? subjectRowSpans(shelfNoEnabled).readonly : GROUP_EXTRA_SPAN[g.key]">
                    <div class="ro-inline">
                      <div class="ro-line">
                        <span class="ro-label">主题词</span>
                        <template v-if="subjectPoints.length">
                          <el-tag v-for="(s, i) in subjectPoints" :key="i" size="small"
                            type="info">{{ s }}</el-tag>
                        </template>
                        <span v-else class="ro-empty">无</span>
                        <div style="flex: 1" />
                        <el-button link type="primary" size="small" @click="tab = 'full'">
                          编辑 →
                        </el-button>
                      </div>
                      <div class="ro-line">
                        <span class="ro-label">责任者</span>
                        <template v-if="responsibilityPoints.length">
                          <el-tag v-for="(s, i) in responsibilityPoints" :key="i" size="small">
                            {{ s }}
                          </el-tag>
                        </template>
                        <span v-else class="ro-empty">无</span>
                      </div>
                    </div>
                  </el-col>
                </el-row>
                </template>
              </template>

              <!-- 文献类型 / 读者对象（2026-10-09 加，见）：**记录级**属性，
                   与排架号同类，不是 CNMARC 字段。读者对象另会被写进 `100$a` 位 17-19
                   （CNMARC 的标准位置）；文献类型取自字典中心「文献类型」。
                   ⚠️ 读者对象是**多选**：该位是 3 字符定长，标准允许从左至右填多个代码
                   （实测真实数据里 `em ` = 青年+普通成人、`cd ` = 小学生+少年）。
                   候选 = 标准原表（`readerLevel.ts`），不是我们猜的。
                   原在右侧录入栏，按用户要求挪进简编表单底部：它们是**编目属性**，
                   与"套录/入库"的操作流分开更合理。两格各 span 12（和为 24 的倍数）。 -->
              <el-row :gutter="12">
                <el-col :span="12" class="field-optional">
                  <el-form-item label="文献类型">
                    <el-select v-model="extra.itemType" filterable allow-create clearable
                      placeholder=" " style="width: 100%">
                      <el-option v-for="x in itemTypes" :key="x.value" :label="x.label" :value="x.value" />
                    </el-select>
                  </el-form-item>
                </el-col>
                <el-col :span="12" class="field-optional">
                  <el-form-item label="读者对象">
                    <el-select v-model="readerLevelCodes" multiple :multiple-limit="READER_LEVEL_MAX"
                      collapse-tags collapse-tags-tooltip filterable allow-create clearable
                      placeholder=" " style="width: 100%">
                      <el-option v-for="r in READER_LEVELS" :key="r.code"
                        :label="r.name" :value="r.code" />
                    </el-select>
                  </el-form-item>
                </el-col>
              </el-row>

              <!-- 「保存入库」已提到页面顶部工具条：不必滚到底部才能保存 -->
            </el-form>
          </el-tab-pane>

          <!-- ===== 全字段 MARC =====
               已抽成 components/MarcPane.vue：可编辑的字段列表（增删字段 / 指示符 /
               子字段），是拆分的**第二步**。
               `fields` 与 `saving` 仍留在页面 —— `fields` 是三个 tab 共享的唯一数据源、
               `saving` 与顶部工具条共用；页面只把「怎么显示、怎么增删」交出去。
               字段中文名查表（`fieldName` / `subfieldName`）也留在页面：字典由
               `loadMarcFields()` 在此拉取并缓存，搬走会改变请求时序。 -->
          <el-tab-pane label="全字段 MARC" name="full">
            <MarcPane :fields="fields" :saving="saving" :field-name="fieldName"
              :subfield-name="subfieldName" @save="saveAndHoldings()" />
          </el-tab-pane>

          <!-- ===== MARC 预览（只读，与另两个视图并列） =====
               已抽成 components/MrkPreview.vue：它输入只有 fields、输出一段字符串，
               与页面其它部分零耦合，是拆分的**第一步**。 -->
          <el-tab-pane label="MARC 预览" name="mrk">
            <MrkPreview :fields="fields" />
          </el-tab-pane>
        </el-tabs>
      </el-col>

      <!-- ===== 侧栏：录入面板 + 馆藏 =====
           质检结果移到顶部提示条，侧栏只留"操作 + 紧邻结果"。
           顶部留出与左侧 tab 标题行等高的间距，让第一张卡片与 tab 下的横线对齐。 -->
      <el-col :span="7" class="side-col"
        style="padding-top: var(--el-tabs-header-height, 40px)">

        <el-card shadow="never" class="entry-panel">
          <template #header>
            <div style="display: flex; align-items: center; gap: 6px">
              <el-icon><EditPen /></el-icon><span>录入</span>
            </div>
          </template>

          <!-- 作业方式胶囊栏（套录模式 / 排架号 / 自动规范 / 规范一次）已搬进
               components/ModeBar.vue（阶段 4a）。
               三个开关的**存储口径各不相同**（localStorage / 按图书库 settings / 全局 settings），
               搬进组件就等于把三套持久化也搬进去，所以组件只做"显示 + 通知"，
               翻状态与落库仍留在本页。 -->
          <ModeBar :catalog-mode="catalogMode" :shelf-no-enabled="shelfNoEnabled"
            :optimize-auto="optimizeAuto" :optimizing="optimizing"
            @toggle-catalog-mode="toggleCatalogMode" @toggle-shelf-no="toggleShelfNo"
            @toggle-optimize="toggleOptimize" @optimize="runOptimize()" />
          <el-form label-width="70px" size="default" @submit.prevent @keydown="onPanelArrow">
            <el-form-item label="馆藏地点">
              <el-select v-model="location" filterable allow-create default-first-option
                placeholder="" style="width: 100%" data-jump="isbn" @blur="commitLocationTyped"
                :title="locationFromMemory ? '已记住上次选择' : '馆藏地点在「字典中心」维护，也可临时输入'">
                <el-option v-for="loc in locationOptions" :key="loc" :label="loc" :value="loc" />
              </el-select>
            </el-form-item>
            <!-- 分区细线：把「馆藏信息」与「套录」在视觉上分开，少一层脑内分组 -->
            <div class="panel-sep" />
            <!-- 就地套录：一个框吃三种输入 —— ISBN / 题名 / 责任者，由后端判断走哪条路。
                 标签写「套录」（原为「ISBN」）——写「套录 ISBN」会在 70px 标签宽里折成两行。
                 框内浅灰占位「ISBN / 题名 / 责任者」是**用户明确要求**的（这一轮同时去掉了悬停提示，
                 没有别的入口说明它能搜题名/责任者）。
                 `data-field="isbn"` 保留旧 key：录入栏的 ↑/↓ 行移动与回车跳格链都按它认字段。 -->
            <el-form-item label="套录" class="lookup-item"
              @click="isbnInput?.focus?.()">
              <el-input ref="isbnInput" v-model="isbnLookup" data-field="isbn"
                placeholder="ISBN / 题名 / 责任者" @keyup.enter.prevent="onLookupEnter">
                <template #append>
                  <!-- 单一入口：ISBN 走一键直取底稿，题名/责任者走通用候选检索。
                       Ctrl+Enter 始终走通用检索，保留比较多个来源的能力。 -->
                  <el-button
                    :icon="isIsbnLike ? Lightning : Search"
                    :loading="quickBusy || looking"
                    :disabled="!isbnLookup.trim()"
                    :aria-label="isIsbnLike ? '一键套录' : '套录检索'"
                    :title="isIsbnLike
                      ? '一键套录（ISBN）；Ctrl+Enter 查看全部候选来源'
                      : '套录检索；Ctrl+Enter 查看全部候选来源'"
                    @click="primaryLookup"
                  />
                </template>
              </el-input>
            </el-form-item>
            <!-- 分类号 + 书次号 + 卷册号 三站一行，直接读作索书号（I247.5/15[:上册]）。
                 分类号是**纯输入框**（原先是对着中图法做远程检索的 el-select）：
                 编目员自己知道分类号，下拉反而抢输入——`default-first-option` 会把回车
                 当成"选第一条"，把刚敲的值悄悄换成另一个分类号；而且每敲一个字就发一次检索。
                 改成纯输入后"敲什么就是什么"，编辑方式与价格框一致。
                 卷册号并进本行（原是独立一行）：三者同属"号"，视觉上应连读。 -->
            <el-form-item label="分类号">
              <!-- 索书号一行三站：分类号 / 书次号 / 卷册号。
                   `/` 由原来的 `#prepend` 插槽改成**独立的窄元素** —— Element 的 prepend
                   addon 自带 padding，实测吃掉约 30px，把书次号挤得显示不全；独立 span 只占
                  一个字符宽。分类号因此从 `flex: 1` 收成 90px 理想宽，腾出的空间还给书次号。
                  宽度是 `flex: 0 1 <理想宽>`：宽屏铺满理想版式，窄屏按比例收缩（详见
                  文件末尾 .callno-row 样式注释）；三控件固定宽在 1440/1366 下会横向溢出
                  侧栏（el-form-item__content 实测仅 ~226px / ~210px），必须允许收缩。 -->
              <div class="callno-row">
                <!-- 分类号直接绑 `simple['690a']` —— 与简编表单组6 **同一个响应式属性**，
                     两处输入框天然同步（与书次号 / 卷册号同一手法 阶段 2）。
                     原先走 `panel.classNo` + usePanelSync 的两个 watch，那是仅剩的两处
                     "镜像缓存"；归一后这一类"一侧比 trim 后、另一侧写原值"的振荡风险整类消失。 -->
                <el-input ref="classNoInput" v-model="simple['690a']" class="callno-cls"
                  data-field="classNo" @keyup.enter="onClassNoEnter" />
                <span class="callno-sep">/</span>
                <el-input ref="bookNoInput" v-model="extra.bookNo" class="callno-no"
                  data-field="bookNo" placeholder="书次号" aria-label="书次号"
                  @input="bookNoTouched = true" @keyup.enter="focusNext('volume')" />
                <!-- 卷册号：**可手输 + 可下拉 + 可清空**（filterable allow-create clearable）。
                     必须带 `el-option` children —— 曾在并入分类号行时误删，导致下拉列表为空、
                     只能手打（用户反馈"恢复之前的样式"）。选项与简编表单组6 同源 `VOLUME_OPTIONS`。 -->
                <el-select ref="volumeInput" v-model="extra.volume" filterable allow-create clearable
                  class="callno-vol" data-field="volume" placeholder="卷册号"
                  @blur="commitVolumeTyped" @keyup.enter="focusNext('price')">
                  <el-option v-for="o in VOLUME_OPTIONS" :key="o.value"
                    :label="o.value" :value="o.value" />
                </el-select>
              </div>
            </el-form-item>
            <!-- 排架号：**记录级**列（→ 905$c），手工录入（**新记录会预填本库上次的值**，
                 见 `applyFormDefaults`）。位置在分类号下面（原来的卷册号行）；受上方开关控制。
                 刻意**不进** PANEL_ROWS：不能打断"条码回车即入库"的既有取舍。
                 框尾 ± 是"同一排书架连编"的省事键：把**结尾数字**加减一（`G-1` → `G-2`）；
                 没有结尾数字（`G` / `架位（一）`）时按钮置灰 —— 不替用户猜格式。
                 两个键必须 `type="button"`，否则在 el-form 里会当提交键。 -->
            <el-form-item v-if="shelfNoEnabled" label="排架号">
              <el-input v-model="extra.shelfNo" class="shelfno-input" :title="SHELF_NO_TITLE"
                @keyup.enter="focusNext('price')">
                <template #append>
                  <span class="shelfno-step">
                    <button type="button" class="shelfno-step__btn" :disabled="!shelfNoCanStep(-1)"
                      title="结尾数字减一（G-2 → G-1）" @click="applyShelfNoStep(-1)">−</button>
                    <button type="button" class="shelfno-step__btn" :disabled="!shelfNoCanStep(1)"
                      title="结尾数字加一（G-1 → G-2）" @click="applyShelfNoStep(1)">+</button>
                  </span>
                </template>
              </el-input>
              <div v-if="shelfNoWarning" class="suggest-line">{{ shelfNoWarning }}</div>
            </el-form-item>
            <!-- 文献类型 / 读者对象已挪到「简编表单」底部（它们是编目属性，
                 不属于套录/入库的操作流）。 -->
            <!-- 分区细线：把「索书号」与「价格/条码」分开 -->
            <div class="panel-sep" />
            <el-form-item label="价格">
              <!-- 价格框 + 结果浮层 + 失败提示整块搬进 components/PriceInput.vue
                   （阶段 4a）。「焦点进入即全选 + 拦住那一次 mouseup」
                   也一起搬了——那是这个框自己的 DOM 行为，留在页面反而要拆成三个 emit。
                   数据仍是页面的：`priceDraft`（草稿）+ 只读的 priceResult / priceCommitError。
                   本格只保留 el-form-item（标签与栅格是页面版面的事）。 -->
              <PriceInput ref="priceInput" v-model="priceDraft" :currency="currency"
                :result="priceResult" :error="priceCommitError"
                @commit="commitPrice" @enter="onPriceEnter" />
            </el-form-item>
            <el-form-item label="条码" required>
              <el-input ref="barcodeInput" v-model="barcodeInputValue" size="large"
                class="barcode-input" style="width: 100%" data-field="barcode"
                @keyup.enter="saveAndHoldings(true)" />
              <!-- 预览行 + 冲突行搬进 components/BarcodePreviewLine.vue（阶段 4a）：
                   两段文本都是 useBarcodePreview 算好的（250ms 去抖 + 最新值获胜），组件零状态。 -->
              <BarcodePreviewLine :preview-text="barcodePreviewText"
                :last-barcode="lastBarcode" :digits="barcodeDigits"
                :conflicts="barcodeConflicts" :library-last="barcodeLibraryLast" />
            </el-form-item>
          </el-form>
          <div v-if="optApplied.length" style="margin-top: 10px">
            <el-tag v-for="(a, i) in optApplied" :key="'o'+i" type="success" size="small"
              style="margin: 2px 4px 0 0">{{ a }}</el-tag>
          </div>

          <!-- 侧栏「馆藏列表」：分割线 + 卷册号/排架号摘要 + 表格 + 已注销提示，
               整块搬到 components/HoldingList.vue（块 F）。
               三个动作都 emit 回本页：save/delete 要动记录字段与二次确认，
               print 走路由（组件不引 useRouter，可独立测）。 -->
          <HoldingList :holdings="holdings" :volume="extra.volume" :shelf-no="extra.shelfNo"
            :shelf-no-enabled="shelfNoEnabled"
            @save="saveHolding" @delete="delHolding" @print="router.push(`/admin/labels?ids=${id}`)" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 套录命中弹窗。两种语义：
         图书库命中 → 同 ISBN 可能是同书的不同卷册/版本 → **二选一**：
                      打开已有条目（加复本）/ 作为新记录编目（另编一册）；
         资源库/Z39.50 命中 → 选一条作为新记录套录。 -->
    <!-- 方向键在弹窗内移动焦点：编目员敲完 ISBN 回车就弹出来，接着应该能全程不碰
         鼠标。`class` 是给 `candFocusables()` 定位根节点用的（el-dialog 可能被
         teleport 到 body，不能靠模板层级找）。 -->
    <el-dialog v-model="candDlg" :title="candInLibrary ? '图书库已有记录' : '套录命中，请选择'"
      class="cand-dlg" width="780px" @keydown="onCandKeydown"
      @opened="onCandOpened" @closed="onCandDialogClosed">
      <el-alert type="info" :closable="false" show-icon style="margin-bottom: 12px"
        :title="candInLibrary
          ? candLibraryHint
          : `共 ${candList.length} 条候选。选定后作为新记录进入套录编辑，填条码回车即可入库。`" />
      <!-- 库里已有 ≠ 只能用它。库内那条可能是多年前手编的、字段残缺，或者
           干脆不是同一本书——给一个明确的出口去看外部源怎么著录。
           做成**实心按钮 + 说明文字**而不是行内链接：链接样式太弱，新编目员看不出
           那里能点（实测反馈）。不自动查：分层检索短路是常态路径能秒回的原因。 -->
      <div v-if="candInLibrary && !candHasExternal" class="cand-more">
        <span class="cand-more-hint">
          当前仅检索本馆图书库。需参考外部编目源的著录时，点此扩展检索。
        </span>
        <el-button :icon="Search" :loading="otherBusy" data-cand-focus
          @click="searchOtherSources()">
          同时检索资源库 / Z39.50
        </el-button>
      </div>
      <div v-for="(c, i) in candList" :key="i" class="cand-item" role="option"
        :tabindex="0" :aria-selected="candSelected === c" data-cand-focus
        :class="{ 'cand-item--on': candSelected === c }"
        @click="onCandRowClick(c)"
        @focus="onCandFocus(c)"
        @keydown.enter.prevent="confirmCandidate(c)"
        @keydown.space.prevent="onCandRowClick(c)">
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
          <el-tag size="small" :type="c.record_id ? 'warning' : 'info'">{{ sourceLabel(c.source) }}</el-tag>
          <b style="font-size: 14px">{{ c.title || '（无题名）' }}</b>
          <span style="color: var(--app-text-muted); font-size: 13px">{{ c.author }}</span>
          <div style="flex: 1"></div>
          <!-- 图书库候选：点行是**选中**（还要再决定打开还是另编），故只显示选中态；
               外部源候选（含"手动拉来的"）：点行即套录，保留按钮语义。 -->
          <el-tag v-if="c.record_id" size="small"
            :type="candSelected === c ? 'success' : 'info'">
            {{ candSelected === c ? '已选' : '选择' }}
          </el-tag>
          <el-button v-else size="small" type="primary">选用</el-button>
        </div>
        <div class="cand-meta">
          <span v-if="c.record_id">记录 #{{ c.record_id }}</span>
          <span v-if="c.volume">卷册号 {{ c.volume }}</span>
          <span v-if="c.publisher">出版者 {{ c.publisher }}</span>
          <span v-if="c.year">出版年 {{ c.year }}</span>
          <span v-if="c.isbn">ISBN {{ c.isbn }}</span>
          <span v-if="c.class_no">分类号 {{ c.class_no }}</span>
        </div>
      </div>
      <!-- 分页：只在"还有下一页"或已经翻过页时出现，避免单条命中时多一行噪音 -->
      <div v-if="candPage > 0 || candHasMore" class="cand-pager">
        <span>第 {{ candPage + 1 }} 页</span>
        <el-button size="small" :disabled="candPage === 0 || looking || otherBusy"
          @click="gotoCandPage(candPage - 1)">上一页</el-button>
        <el-button size="small" :disabled="!candHasMore || looking || otherBusy"
          @click="gotoCandPage(candPage + 1)">下一页</el-button>
      </div>
      <template #footer>
        <template v-if="candInLibrary">
          <el-button @click="candDlg = false">取消</el-button>
          <el-button :disabled="!candSelected" @click="catalogSelectedAsNew">
            作为新记录编目（另编一册）
          </el-button>
          <el-button ref="candOpenBtn" type="primary" :disabled="!candSelected"
            @click="openSelectedLibrary">
            打开已有条目（加复本）
          </el-button>
        </template>
        <el-button v-else @click="candDlg = false">取消</el-button>
      </template>
    </el-dialog>

    <!-- 质检未通过：列出 ERROR 并给出出路（自动修复 / 返回修改 / 强制入库兜底） -->
    <el-dialog v-model="gateDlg" title="质检未通过，尚未保存" width="600px">
      <el-alert type="error" :closable="false" show-icon
        title="以下各项须修正后才能入库"
        :description="`共 ${gateErrors.length} 项。`" />
      <div style="margin-top: 12px">
        <div v-for="(e, i) in gateErrors" :key="i"
          style="display: flex; gap: 10px; align-items: flex-start; padding: 8px 0; border-bottom: 1px solid var(--app-border)">
          <el-tag size="small" type="danger" style="flex: 0 0 auto">
            {{ e.rule_name || e.rule_id }}
          </el-tag>
          <div style="min-width: 0">
            <!-- 主行说人话（plain，如「这个 ISBN 号码对不上」）；技术细节（哪个字段、什么值）
                 降为小灰字跟在后面，专业用户可以核对、非专业用户不必读。 -->
            <div style="font-size: 13px; line-height: 1.5">
              {{ e.plain || e.message }}
              <span v-if="e.plain" style="color: var(--app-text-muted); font-size: 12px">（{{ e.message }}）</span>
            </div>
            <div v-if="e.hint" style="font-size: 12px; color: var(--app-text-muted); line-height: 1.5; margin-top: 2px">
              {{ e.hint }}
            </div>
          </div>
        </div>
      </div>
      <el-alert v-if="isNew" type="info" :closable="false" style="margin-top: 12px"
        title="尚未入库，可先尝试自动修复"
        description="只改当前编辑内容；仍不能通过时可强制入库。" />
      <template #footer>
        <el-button @click="gateDlg = false">返回修改</el-button>
        <el-button type="warning" :loading="saving" @click="forceSave">仍要保存（强制入库）</el-button>
        <el-button type="primary" :loading="optimizing" @click="optimizeFromGate">
          尝试自动修复
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch, nextTick } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { EditPen, Lightning, Search } from '@element-plus/icons-vue'
import { createRecord, getRecord, addHoldings, getShelfNoSetting,
         putShelfNoSetting,
         deleteHolding, deleteRecord, listHoldings, lookupLanguages,
         lookupPlaces, updateRecord, http, apiErrorText,
         getDefaultCurrency, getDictionary } from '../api'
import type { Finding, MarcField } from '../api'
import { parseRecordRoute } from '../recordRoute'
import { barcodeDigitsAllowed, barcodeWidth, digitList } from '../barcodeInput'
import { VOLUME_OPTIONS, bookNoFromCallNo, bookNoForSave, deriveVolumeSeq,
         planClassNoEnter, shelfNoSoftWarning } from '../volume'
import {
  BINDINGS, BOUND_TAGS,
  bindingsInGroup, CHIP_MAX, colSpan, EDITION_SUGGESTIONS, GROUPS, GROUP_EXTRA_SPAN,
  simpleKey,
  SIZE_SUGGESTIONS, stripUnit, subjectRowSpans,
} from '../simpleForm'
import type { Binding } from '../simpleForm'
import { evalPriceExpression, formatPrice, isPriceExpression } from '../priceExpr'
import { READER_LEVELS, READER_LEVEL_MAX, joinReaderLevel, splitReaderLevel } from '../readerLevel'
import { sourceLabel } from '../../lib/displayLabels'
import { useBarcodePreview } from '../composables/useBarcodePreview'
import { useBarcodeSequencer } from '../composables/useBarcodeSequencer'
import { useIsbnPrefill } from '../composables/useIsbnPrefill'
import { useMarcDictionary } from '../composables/useMarcDictionary'
import { useQualityGate } from '../composables/useQualityGate'
import { useReplacementGuard } from '../composables/useReplacementGuard'
import { useOptimize } from '../composables/useOptimize'
import { useSaveBaseline } from '../composables/useSaveBaseline'
import { useBookNoSync } from '../composables/useBookNoSync'
import { usePanelSync } from '../composables/usePanelSync'
import { useShelfNoMemo } from '../composables/useShelfNoMemo'
import { useCatalogLookup } from '../composables/useCatalogLookup'
import { usePanelNav } from '../composables/usePanelNav'
import { useSimpleKeyNav } from '../composables/useSimpleKeyNav'
// 「MARC 预览」tab 整块（模板 + MRK 文本生成 + 样式）已抽成独立组件
import MrkPreview from '../components/MrkPreview.vue'
import MarcPane from '../components/MarcPane.vue'
// 顶部工具条 + 质检提示条（拆自本文件模板 3–32 行） 块 A
import EditorToolbar from '../components/EditorToolbar.vue'
// 侧栏「馆藏列表」（拆自本文件模板 + inactiveHoldingCount） 块 F
import HoldingList from '../components/HoldingList.vue'
// 作业方式胶囊栏 + 条码预览行 + 价格框（拆自本文件模板） 阶段 4a
import ModeBar from '../components/ModeBar.vue'
import BarcodePreviewLine from '../components/BarcodePreviewLine.vue'
import PriceInput from '../components/PriceInput.vue'

const route = useRoute(); const router = useRouter()

// 全部做成 computed：路由 /records/:id 的参数变化时 Vue **复用同一组件实例**
// （不会重新 mount），若在 setup 里算一次，保存后 router.replace 到新记录时
// 这些值仍是旧的，界面就不刷新——表现为"保存成功但页面没变"。
const id = computed(() => {
  const param = String(route.params.id ?? '')
  if (param) return param
  // 静态路由 /admin/records/new 与 /admin/records/temp 没有 :id 参数，
  // 旧版编辑器按路径末段判定 new / temp。
  return route.path.split('/').filter(Boolean).pop() || ''
})

// 路由参数语义统一在 recordRoute.ts 判定（'new' | 'temp' | 数字 id）。
// 直接 Number(id) 会得到 NaN，拼进 /records/${id} 就变成 /records/temp/... 被 422 拒绝。
const parsedRoute = computed(() => parseRecordRoute(id.value))
const isNew = computed(() => parsedRoute.value.kind === 'new')
const isTemp = computed(() => parsedRoute.value.kind === 'temp')  // 套录/资源库临时副本
// 已入库记录的数字 id；未入库（新建/临时副本）为 null。记录级请求一律用它。
const recordIdOrNull = computed<number | null>(() => parsedRoute.value.recordId)
const loading = ref(false); const saving = ref(false)
const forcing = ref(false)      // 质检未通过时用户确认"仍要保存"，本次请求带 force
const tab = ref('simple')
const fields = ref<MarcField[]>([])

// 控制字段判定（`isControlField`）、增删字段/子字段（`addSub` / `addField`）
// 已随「全字段 MARC」tab 一起搬进 components/MarcPane.vue。
// 它们只被那一个 tab 用，留在这里等于把 pane 的实现细节摊在页面上。
const findings = ref<Finding[]>([])
const hasError = computed(() => findings.value.some(f => f.level === 'ERROR'))
// 顶部提示条只列规则名（如「出版年格式、主题词缺失」）保持一行可读；
// 具体问题与修复建议在保存被拦时的对话框里展开。
const qualitySummary = computed(() => {
  const fs = findings.value
  const names = fs.slice(0, 4).map(f => f.rule_name || f.rule_id)
  const rest = fs.length > 4 ? ` 等 ${fs.length} 项` : ''
  // 先讲清「拦不拦得住」：有 ERROR 是必须修的（保存会被拒），否则只是建议。
  const lead = hasError.value ? '以下问题必须先修好才能入库' : '以下地方建议完善'
  return `${lead}：${names.join('、')}${rest}`
})
const detail = ref<any>(null)
// 套录/资源库临时副本的 leader（未入库，不在 detail 里，但保存时应带上）
const tempLeader = ref<string | null>(null)
// 当前编辑所属图书库：已保存记录用记录自身的库，新建/临时副本用列表页选中的库。
// 条码流水与查重都按库隔离，取错库会导致号段串库。（定义在 detail 之后，避免 TDZ）
const currentLibId = computed<number | undefined>(() => {
  if (detail.value?.library_id) return Number(detail.value.library_id)
  const v = Number(localStorage.getItem('jianji.library'))
  return v || undefined
})
// 馆藏地点：记住上次选择，避免每本书都重选一遍
const LOCATION_KEY = 'cataloging_last_location'
const rememberedLocation = localStorage.getItem(LOCATION_KEY) || ''
const location = ref(rememberedLocation)
const locationFromMemory = computed(
  () => !!rememberedLocation && location.value === rememberedLocation)
watch(location, v => {
  try { localStorage.setItem(LOCATION_KEY, v || '') } catch { /* 忽略隐私模式限制 */ }
})
const holdings = ref<any[]>([])
// 转圈状态（优化进行中）。优化一族（模式开关 / runOptimize / 改动明细）抽到
// useOptimize；这个 ref **刻意留在本组件**——工具条、质检门槛按钮、替换守卫的
// onReset 三方共用，硬搬进 composable 只会把 ref 原样漏回去（与 usePanelSync
// 保留 simple/fields 同一取舍），以 Ref 注入。
const optimizing = ref(false)

// 「套录模式」（作业方式胶囊之一）：决定**条码回车保存后光标去哪**。
//   'catalog'（默认）= 回 ISBN 框，编目一本接一本（下一动作是套录下一本）；
//   'barcode'        = 留在条码框，给同一条记录连续贴多册（保存时条码框已被清空）。
// 存 localStorage：属**个人作业习惯**（与「语种默认值」「馆藏地点记忆」同一类），
// 不发请求、不跨机，也不该被别的编目员顺手改掉。读取失败（隐私模式）按默认处理。
const CATALOG_MODE_KEY = 'cataloging_mode'
const catalogMode = ref<'catalog' | 'barcode'>(
  localStorage.getItem(CATALOG_MODE_KEY) === 'barcode' ? 'barcode' : 'catalog')
function toggleCatalogMode() {
  catalogMode.value = catalogMode.value === 'catalog' ? 'barcode' : 'catalog'
  try { localStorage.setItem(CATALOG_MODE_KEY, catalogMode.value) }
  catch { /* 隐私模式：只在本次会话生效 */ }
}

// ===== 录入面板状态 =====
const classNoInput = ref()
const priceInput = ref()
const barcodeInput = ref()
const bookNoInput = ref()
const volumeInput = ref()
// `panel` 这个"什么都装的袋子"已按 彻底撤销：
//   bookNo → extra.bookNo（阶段 1）、classNo → simple['690a']（阶段 2）、
//   priceNum → 下面的 priceDraft（阶段 3）。
/**
 * 价格框的**草稿**（阶段 3）。
 *
 * ⚠️ 它**不是** `010$d` 的镜像 —— 那个值是 `simple['010d']`。
 * 这个框支持**算式**（`135/5` = 整套总价 ÷ 册数），用户正在敲、还没提交的算式
 * **绝不能**落进 MARC（会存成 `CNY135/5`，静默写脏数据）。两道守卫拦它：
 *   ① usePanelSync 里"只有纯数字才写回 010$d"；
 *   ② currentPayloadFields / applyPanelToFields 再拦一道。
 * 单独起名 `priceDraft` 就是为了让"这是草稿、不是值"这层语义在代码里看得见 ——
 * 原先它叫 `panel.priceNum`，混在一个"什么都装的袋子"里，从名字上完全看不出。
 */
const priceDraft = ref('')

// ===== 记录级业务字段：书次号 / 卷册号 / 排架号 =====
// 它们是 **records 表的列，不是 CNMARC 子字段**，所以走独立的 extra 状态，
// 绝不进 simpleForm 的 BINDINGS（README 规则 12 的防线）。
// 录入面板（常驻侧栏）与简编表单 tab 绑定**同一份** extra → 天然双向同步（规则 13）。
//
// `bookNo`（记录级列 `book_no`）原先是 `panel.bookNo`，2026-10-08 并进这里：
// 它和卷册号 / 排架号同类（都是"种级"的记录列），本就该同处一个容器 ——
//  那张表的记录级三行现在终于落在同一份状态里。
const extra = reactive<{
  bookNo: string; volume: string; volumeSeq: number | null; shelfNo: string
  // 文献类型 / 读者对象（2026-10-09）：记录级属性。
  itemType: string; readerLevel: string
}>({ bookNo: '', volume: '', volumeSeq: null, shelfNo: '',
     itemType: '', readerLevel: '' })
// 排架号开关（**按图书库**，存 settings `lib{id}|shelf_no`）；默认 false（关闭）。
// 关闭 = 界面上看不到排架号控件，**不等于**数据里没有排架号。
const shelfNoEnabled = ref(false)
// 排架号软校验提示（不阻断保存，只在框下方显示一行灰字）
const shelfNoWarning = computed(() => shelfNoSoftWarning(extra.shelfNo))

/**
 * 排架号框的悬停提示（**录入栏与简编表单组6 共用同一份文案**，别再各写一遍）。
 *
 * ⚠️ 必须保留「**系统不自动生成**」这五个字：`recordEditSimpleForm.test.ts` 用它判定
 * "排架号开关关掉后框不渲染"。
 *
 * 2026-10-06 起**去掉了原来的「不预填」** —— 用户裁定新记录预填本图书库上次保存的排架号
 * （连编同一架书不必重敲）。那是**唯一**的例外，其余"别好心给默认值"照旧：不校验唯一性、
 * 不替用户猜格式（无结尾数字时 ± 按钮直接置灰）。详见 修订段。
 */
const SHELF_NO_TITLE =
  '纯手工录入：系统不自动生成、不参与索书号拼接；留空不阻断保存。'
  + '新记录会预填本图书库上次保存的排架号；框尾 ± 可把结尾数字加减一。'

// 排架号记忆与框尾 ± 步进：抽到 useShelfNoMemo —— 按图书库分别记（换库不串），
// 存 localStorage，与「馆藏地点」「语种」同属**个人作业习惯**。
const { rememberedShelfNo, rememberShelfNo, shelfNoCanStep, applyShelfNoStep } =
  useShelfNoMemo({ extra, currentLibId })

// 卷册序自动带出（P0 无输入框：volume_seq 本轮没有消费方，隐式跟随 volume 变化）
watch(() => extra.volume, v => { extra.volumeSeq = deriveVolumeSeq(v ?? '') }, { flush: 'sync' })
const locationOptions = ref<string[]>([])
/** 文献类型候选（字典中心 `item_types`）。 */
const itemTypes = ref<Array<{ value: string; label: string }>>([])

/**
 * 读者对象：界面用**多选数组**，模型里是 `100$a` 位 17-19 的 **3 位定长串**。
 *
 * 标准（GB/T 33286—2016）允许从左至右填多个代码、不足三位补空格，
 * 所以 `em` ⇄ `['e','m']`、清空 ⇄ `''`。候选表与 join/split 都在 `readerLevel.ts`
 * （那边带标准原文与判据）。
 */
const readerLevelCodes = computed<string[]>({
  get: () => splitReaderLevel(extra.readerLevel),
  // 清空时给空串：后端 `_apply_reader_level` 对空串是「**不改**」（不是清空）——
  // 这是刻意的安全取舍（套录分支不灌这个值），见 `api/records.py` 的 docstring。
  set: v => { extra.readerLevel = v && v.length ? joinReaderLevel(v) : '' },
})
// 本馆默认币种（「字典中心 → 货币类型」里设）：定价框的货币码 + 码洋统计口径。
// 兜底 'CNY' —— 拿不到设置时不至于让定价框空着（与 optimizeMode 同一降级风格）。
const currency = ref('CNY')
const barcodeInputValue = ref('')     // 支持 +N / -N / 条码号
// 条码续号 / 解析 / 收集抽到 useBarcodeSequencer（+N/-N 语义、位数校验的唯一
// 实现）。条码预览与冲突检测在 useBarcodePreview（250ms 去抖 + "最新值获胜"
// 守卫），后者需要 parseBarcodeInput 作为注入依赖——从这里取再传下去。
const {
  barcodeDigits, lastBarcode, barcodeLibraryLast,
  refreshLastBarcode, parseBarcodeInput, collectBarcodes,
} = useBarcodeSequencer({ currentLibId, barcodeInputValue, barcodeInput })
// 出版者反查的"同号只查一次"标记在 useIsbnPrefill（resetPrefill 负责清）
// 简编表单的字典候选（一次取回，本地筛选；不在输入时发请求）
const languages = ref<{ code: string; name: string }[]>([])
const commonPlaces = ref<string[]>([])
// 责任方式候选（`200$f/$g` 下方那排 chip）：来自「字典中心 → 责任方式」（内置 22 条）
const responsibilityOptions = ref<string[]>([])
// 条码预览与冲突检测抽到 useBarcodePreview（250ms 去抖 + "最新值获胜"守卫）。
const { barcodePreviewText, barcodeConflicts, resetBarcodePreview,
        dispose: disposeBarcodePreview } =
  useBarcodePreview({
    barcodeInputValue, recordIdOrNull, currentLibId,
    parseBarcodeInput: (input: string) => parseBarcodeInput(input),
  })

// 简编表单的字段绑定与双向转换都在 src/simpleForm.ts（纯逻辑，有单测）。
// 那里的核心不变式是：**只重建"表单真正承载了内容"的字段实例，其余一律保留** ——
// 否则第二个 200（合订书名）、200$h（分册）、225$v（卷次）、第二个 010 会被静默吞掉。
const simple = reactive<Record<string, string>>({})
BINDINGS.forEach(b => (simple[simpleKey(b.tag, b.code)] = ''))

/**
 * 组6 是**整块自定义**的（分类号 / 书次号 / 卷册号 三格一行），不走模板的通用分支，
 * 所以它的标签与「必备」红星要从 `BINDINGS` 里取 —— 不另抄一份 `分类号` / `required`，
 * 否则以后改了 BINDINGS，这里的标签不会跟着变。
 */
const classNoBinding = BINDINGS.find(b => b.tag === '690' && b.code === 'a')!

// 核心字段保持白底；下列补充字段只做视觉降级，不折叠、不改字段顺序。
const OPTIONAL_KEYS = new Set([
  '010b', '010d',
  '200d', '200e', '200g',
  '205a',
  '215c', '215d', '215e',
  '225a', '225h', '225v',
  '330a', '300a',
])
function isOptional(b: Binding): boolean {
  return OPTIONAL_KEYS.has(simpleKey(b.tag, b.code))
}
const callNoPreview = computed(() => {
  const cls = (simple['690a'] || '').trim()
  const bookNo = (extra.bookNo || '').trim()
  const volume = (extra.volume || '').trim()
  if (!cls && !bookNo) return ''
  if (!cls) return `/${bookNo}${volume ? `:${volume}` : ''}`
  const base = `${cls}${bookNo ? `/${bookNo}` : ''}`
  return volume ? `${base}:${volume}` : base
})
const callNoIncomplete = computed(() =>
  !(simple['690a'] || '').trim() && Boolean((extra.bookNo || '').trim()))

// ===== 语种：默认汉语 + 记住上次选择 =====
// 与「馆藏地点」同一套做法（见上方 LOCATION_KEY）。两条口径：
//   1. **默认** `chi`（汉语）—— 小学馆绝大多数是中文书，不该每本都选一次；
//   2. **记住上次选择** —— 只在**用户真正改过**时写入，所以挂在 select 的 `@change` 上，
//      而不是 watch：watch 会把"打开一条英文书记录"也当成一次选择，把默认值带偏。
const LANGUAGE_KEY = 'cataloging_last_language'
const DEFAULT_LANGUAGE = 'chi'
const rememberedLanguage = localStorage.getItem(LANGUAGE_KEY) || DEFAULT_LANGUAGE

function onLanguageChange(v: string) {
  try { if (v) localStorage.setItem(LANGUAGE_KEY, v) } catch { /* 忽略隐私模式限制 */ }
}

/**
 * 新建记录 / 套录底稿的**默认值兜底**（内部先语种、后装订）：
 *   ① 语种（`101$a`）为空 → 填"上次选择（首次为汉语）"；
 *   ② 装订（`010$b`）为空 → 填"平装"。
 *
 * **存量记录一律不动** —— 与「分类号截取」同一口径：默认值只给新数据。
 * 只在 `load()` 的 `isNew` / `isTemp` 分支调用（共三处），套录来源带了 `010$b`
 * （如「精装」）时**有值不动**，仅空值兜底；`010$b` 候选含 `'平装'`
 * （见 `simpleForm.ts::BINDING_OPTIONS`），下拉会正常显示为已选中。
 */
function applyFormDefaults() {
  if (!isNew.value && !isTemp.value) return
  if (!(simple['101a'] ?? '').trim()) simple['101a'] = rememberedLanguage
  if (!(simple['010b'] ?? '').trim()) simple['010b'] = '平装'
  // 排架号：新记录 / 套录底稿**预填本图书库上次保存的值**（2026-10-06 用户裁定；
  // 连编同一架书时不必重敲）。两道守卫，缺一不可：
  //   ① **开关打开才填** —— 开关关着时这个框根本不渲染，填进去就是"看不见却会落库"，
  //      属于本项目最忌讳的静默写数据；
  //   ② **框为空才填** —— 不覆盖用户已填 / 套录底稿带进来的值。
  // 存量记录一律不动（与上面语种 / 装订同一口径：默认值只给新数据）。
  if (shelfNoEnabled.value && !(extra.shelfNo ?? '').trim()) {
    extra.shelfNo = rememberedShelfNo()
  }
}

/** 简编表单覆盖的字段标签 */
const COVERED_TAGS = new Set(BOUND_TAGS)
/** 不提示的系统字段：001 记录控制号、905 馆藏（由馆藏表重建） */
const SYSTEM_TAGS = new Set(['001', '905'])
/**
 * 简编 tab **可见（只读区块）** 的字段判据——**唯一真源**。
 *
 * 只读区块的渲染与 `hiddenFields` 的计数**必须**共用这套判据，否则两处会分叉：
 * 例如只读区块用 `/^7[0-2]\d$/`（覆盖 700–729）而计数只列了 8 个 tag 时，
 * 含 `703`/`721` 的记录区块**显示了**、上方提示条却还报"未显示"，同一屏自相矛盾。
 *
 * 拆成三个：`isSubjectTag` / `isResponsibilityTag` 是各自区块的**唯一**匹配条件，
 * `isReadonlyDisplayTag` 是二者之并——`hiddenFields` 用它，天然与两个区块口径一致。
 * 注意只覆盖 606 与 7xx；其余 6xx/86x 主题字段本次不做区块，仍照常计数。
 */
const isSubjectTag = (tag: string): boolean => tag === '606'
const isResponsibilityTag = (tag: string): boolean => /^7[0-2]\d$/.test(tag)
const isReadonlyDisplayTag = (tag: string): boolean =>
  isSubjectTag(tag) || isResponsibilityTag(tag)

/**
 * 简编表单看不见、但记录里确实存在的字段。
 *
 * 这些字段保存时会原样保留（见 simpleForm.buildFromSimple），但界面上完全不可见——
 * 不提示的话编目员会以为记录就这些内容，也就不会去补 701/606。
 *
 * 例外：`isReadonlyDisplayTag`（606 / 7xx）已在只读检索点区块里显示，故不在此计数。
 */
const hiddenFields = computed(() => {
  const tags = new Set<string>()
  let count = 0
  for (const f of fields.value) {
    if (COVERED_TAGS.has(f.tag) || SYSTEM_TAGS.has(f.tag)
      || isReadonlyDisplayTag(f.tag)) continue
    tags.add(f.tag)
    count += 1
  }
  return { tags: [...tags].sort(), count }
})

/** 数值字段右对齐的 class（对齐老系统 `字段定义文件` 的 MiddleRight）。 */
function numClass(b: Binding): string {
  return b.align === 'right' ? 'num-input' : ''
}

/**
 * 只读「主题词」检索点：记录中所有 606 的 $a（含 $x$y$z 复分，拼成完整串）。
 * 做成只读是刻意的：多值检索点在简编表单只有一个输入框（规则 12），
 * 可编辑会变成"只改到第一条"的隐蔽陷阱。
 */
const subjectPoints = computed<string[]>(() => {
  const out: string[] = []
  for (const f of fields.value) {
    if (!isSubjectTag(f.tag)) continue
    const parts = (f.sf ?? [])
      .filter(s => ['a', 'x', 'y', 'z'].includes(s[0]))
      .map(s => (s[1] ?? '').trim())
      .filter(Boolean)
    if (parts.length) out.push(parts.join('—'))
  }
  return out
})

/** 只读「责任者检索点」：所有 70x/71x/72x 的 $a（+$4 著作责任，若有则显示为「张三 著」）。 */
const responsibilityPoints = computed<string[]>(() => {
  const out: string[] = []
  for (const f of fields.value) {
    if (!isResponsibilityTag(f.tag)) continue
    const sf = f.sf ?? []
    const name = (sf.find(s => s[0] === 'a')?.[1] ?? '').trim()
    if (!name) continue
    const duty = (sf.find(s => s[0] === '4')?.[1] ?? '').trim()
    out.push(duty ? `${name} ${duty}` : name)
  }
  return out
})

// 已注销 / 已遗失的册数（原 `inactiveHoldingCount`）随「馆藏列表」块搬进
// components/HoldingList.vue（块 F）——该列表是本页唯一的使用处。

// MRK 文本的生成已随「MARC 预览」tab 一起搬进 components/MrkPreview.vue

// ---- 简编表单 ↔ 录入面板 ↔ MARC 的双向同步（实现抽到 composables/usePanelSync.ts）----
// 数据源归一（阶段 2/3）后只剩**定价**一项 watch：分类号两处已直接绑
// 同一个 `simple['690a']`，而价格框装的是草稿 `priceDraft`（不是 010$d 的镜像）。
// 此外还有 tab 切换的双向重建与保存载荷唯一出口 currentPayloadFields。
// `simple`/`priceDraft`/`currency`/`tab`/`fields` 仍是本组件的状态，
// composable 只是把这些联动收敛到一处（设计取舍与全部守卫的注释都在那边）。
const { populateSimple, currentPayloadFields } =
  usePanelSync({ simple, priceDraft, currency, tab, fields })

// 所有整稿替换共用身份、请求序号、编辑版本协议；继续输入无需锁住编辑器。
let recordEpoch = 0
let loadSeq = 0
let disposed = false
const editVersion = ref(0)
const currentLeader = computed(() => detail.value?.leader ?? tempLeader.value)
const recordIdentity = () => `${id.value}:${recordEpoch}:${currentLibId.value ?? ''}`
watch([fields, simple, extra, priceDraft, currentLeader, location, barcodeInputValue], () => {
  editVersion.value += 1
}, { deep: true, flush: 'sync' })

function editSnapshot() {
  return JSON.stringify({ fields: currentPayloadFields(), leader: currentLeader.value ?? null,
    extra: { ...extra }, location: location.value, barcode: barcodeInputValue.value,
    // 未提交算式同样属于未保存输入，即使尚未进入 MARC。
    priceDraft: priceDraft.value })
}

// 保存基线 / 脏状态机（savedBaseline / draftPendingSave / isDirty）抽到
// useSaveBaseline——第三/四刀刻意搁置的债到此收口：保存流程对基线的 5 处
// 外科手术式直写全部变成有名字的操作（commitBaseline / preserveOldBarcode…）。
// 快照序列化 editSnapshot 留在本组件：它要读全部编辑态，经 snapshot 注入。
const { savedBaseline, isDirty, markSavedBaseline, resetBaseline,
        adoptBaseline, flagDraftPending, commitBaseline, preserveOldBarcode } =
  useSaveBaseline({ snapshot: editSnapshot })

// 替换令牌机（身份/序号/版本三令牌 + 作废）抽到 useReplacementGuard——保存主
// 流程、套录安装、规则优化全靠它做四重守卫。
const { beginReplacement, replacementIsCurrent, isLatestSeq, invalidateReplacements } =
  useReplacementGuard({ recordIdentity, isDisposed: () => disposed, editVersion,
                        onReset: () => { optimizing.value = false } })

// 优化一族（模式开关 / 自动规范策略 / runOptimize / 改动明细标签）抽到 useOptimize。
// 编辑态的整稿安装是组件形状的写入，经 installResult 回调留在本组件
// （installOptimizeResult，与 installCatalogDraft 同类），见 composable 类注释。
const { optimizeMode, optimizeAuto, optApplied, loadOptimizeMode, autoOptimizeAfterLoad,
        toggleOptimize, runOptimize } =
  useOptimize({
    optimizing, guard: { beginReplacement, replacementIsCurrent, isLatestSeq },
    getPayload: () => ({ leader: currentLeader.value, fields: currentPayloadFields() }),
    installResult: installOptimizeResult,
    isNew: () => isNew.value, isTemp: () => isTemp.value,
  })

// 质检门槛状态机（gateDlg / gateErrors / openGate / forceSave / optimizeFromGate）。
// runOptimize 来自上面的 useOptimize（const，先于门槛创建）；recordIdentity 已在上面定义。
const { gateDlg, gateErrors, openGate, forceSave, optimizeFromGate, resetGate } = useQualityGate({
  recordIdentity, isDisposed: () => disposed, runOptimize,
})

// ISBN → 出版者/出版地反查预填（"建议不覆盖"口径 + 身份/版本双守卫见 composable）
const { fillPublisherFromIsbn, onSimpleBlur, resetPrefill } =
  useIsbnPrefill({ simple, recordIdentity, editVersion, isDisposed: () => disposed })
async function confirmDiscard() {
  if (!isDirty.value) return true
  try {
    await ElMessageBox.confirm('当前内容尚未保存，离开或替换后将丢失这些修改。', '未保存的修改', {
      type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑',
    })
    return true
  } catch { return false }
}
// 套录跳转已确认时只放行那一个目的地；不提前清除脏状态。
let approvedNavigation: string | null = null
let savedNavigation: string | null = null
async function guardNavigation(to: { fullPath: string }) {
  if (approvedNavigation === to.fullPath) { approvedNavigation = null; return true }
  return confirmDiscard()
}
onBeforeRouteLeave(guardNavigation)
onBeforeRouteUpdate((to, from) => to.path === from.path ? true : guardNavigation(to))
function onBeforeUnload(event: BeforeUnloadEvent) {
  if (!isDirty.value) return
  if (isTemp.value) syncTemp()
  event.preventDefault()
  event.returnValue = ''
}
watch(editVersion, () => {
  if (isTemp.value && !loading.value && !disposed) syncTemp()
}, { flush: 'post' })

/**
 * 卷册号 / 排架号的载荷片段（**记录级列，不是 MARC 字段**，故单独拼）。
 *
 * 三处保存（新建 / 保存入库 / 改条码后同步记录）都必须显式带上它 —— extra 不在
 * `currentPayloadFields()` 里（那个函数只处理 MARC 字段）。
 * `volume_seq` 传 undefined 时键会被省略，后端按 volume 自动带出（含清空）。
 */
function extraFields() {
  return {
    volume: extra.volume ?? '',
    volume_seq: extra.volumeSeq ?? undefined,
    shelf_no: extra.shelfNo ?? '',
    // 记录级属性：空串 = 显式清空（后端把 "" 当"清空"，None 当"不改"）
    item_type: extra.itemType ?? '',
    // ⚠️ `reader_level` 是**例外**：空串在后端是「**不改**」不是「清空」——
    // 套录/临时副本那条分支不灌这个值，当清空会静默抹掉来源的读者对象。
    // 完整理由见 `api/records.py::_apply_reader_level` 的 docstring。
    // ⇒ 界面**清不空这一格**（要清走「全字段 MARC」直接改 `100$a`）。
    reader_level: extra.readerLevel ?? '',
  }
}

// 载入完成后的光标落点。默认落分类号（从列表点进来先核对索书号）；
// 保存新记录后按「套录模式」置成 'isbn'（编下一本）或 'barcode'（继续贴码）。
let focusAfterLoad: 'isbn' | 'classNo' | 'barcode' | null = null

function populateExtra(record: any) {
  extra.bookNo = record.book_no ?? record.extra?.bookNo ?? ''
  extra.volume = record.volume ?? record.extra?.volume ?? ''
  extra.volumeSeq = record.volume_seq !== undefined ? record.volume_seq
    : record.extra?.volumeSeq !== undefined ? record.extra.volumeSeq : deriveVolumeSeq(extra.volume)
  extra.shelfNo = record.shelf_no ?? record.extra?.shelfNo ?? ''
  extra.itemType = record.item_type ?? record.extra?.itemType ?? ''
  extra.readerLevel = record.reader_level ?? record.extra?.readerLevel ?? ''
}

/**
 * 把光标送到条码框（「打开已有条目（加复本）」落地时用）。
 *
 * 跳转本身已经推迟到候选弹窗彻底关闭之后（见 useCatalogLookup.onCandDialogClosed），
 * 焦点陷阱不会再抢，所以正常一次 focus() 就够。这里只留一次短重试，防的是
 * 数据还没载完、输入框尚未渲染出来的那一瞬。
 */
function focusBarcodeAfterDialog() {
  nextTick(() => {
    barcodeInput.value?.focus?.()
    window.setTimeout(() => {
      const el = document.querySelector<HTMLElement>('input[data-field="barcode"]')
      if (el && document.activeElement !== el) el.focus()
    }, 120)
  })
}

async function load() {
  const seq = ++loadSeq
  const routeId = id.value
  const current = () => !disposed && seq === loadSeq && routeId === id.value
  recordEpoch += 1
  invalidateReplacements()
  invalidateBookNoSync()
  resetBaseline()
  loading.value = true
  try {
    barcodeInputValue.value = ''
    resetBarcodePreview()
    resetLookup()
    optApplied.value = []
    findings.value = []
    holdings.value = []
    resetGate()
    detail.value = null // 新建/底稿不得继续使用上一条记录的图书库
    tempLeader.value = null
    fields.value = []
    populateSimple([]) // 所有简编值清空，不能只清分类号
    populateExtra({})
    bookNoTouched.value = false
    loadedClassNo = ''
    loadedBookNo = ''
    resetPrefill()
    await loadCurrency()
    if (!current()) return
    await loadMarcFields()
    if (!current()) return
    let tempPayload: any = null
    if (isNew.value || isTemp.value) {
      const raw = isTemp.value ? sessionStorage.getItem('cataloging_temp') : null
      tempPayload = raw ? JSON.parse(raw) : null
      flagDraftPending(!!tempPayload)
      fields.value = tempPayload?.fields ?? [{ tag: '001', data: '' }]
      tempLeader.value = tempPayload?.leader ?? null
      populateExtra(tempPayload ?? {})
      // 手敲标志随底稿恢复（写入见 syncTemp）；新开表单保持 false → 预览号不回传
      bookNoTouched.value = !!tempPayload?.book_no_touched
      loadedBookNo = (extra.bookNo || '').trim()
      if (tempPayload?.location !== undefined) location.value = tempPayload.location
      barcodeInputValue.value = tempPayload?.barcode ?? ''
    } else {
      const rid = recordIdOrNull.value!
      const d = await getRecord(rid)
      if (!current()) return
      const h = await listHoldings(rid)
      if (!current()) return
      detail.value = d
      fields.value = JSON.parse(JSON.stringify(d.fields))
      findings.value = d.findings ?? []
      holdings.value = h.holdings
      // 载入抑制保持后端归一分类号口径，书次号优先记录列，兼容旧馆藏派生值。
      loadedClassNo = (d.search?.class_no ?? '').trim()
      populateExtra(d)
      extra.bookNo = d.book_no ?? bookNoFromCallNo(holdings.value[0]?.call_no)
      loadedBookNo = (extra.bookNo || '').trim()
    }
    populateSimple(fields.value)
    if (tempPayload && extra.bookNo) loadedClassNo = (tempPayload.search?.class_no ?? simple['690a'] ?? '').trim()
    if (tempPayload) loadedBookNo = (extra.bookNo || '').trim()
    await loadCommon()
    if (!current()) return
    applyFormDefaults()
    if (tempPayload?.priceDraft !== undefined) priceDraft.value = tempPayload.priceDraft
    await nextTick()
    if (!current()) return
    markSavedBaseline()
    if (tempPayload?.savedBaseline) adoptBaseline(tempPayload.savedBaseline)
    loading.value = false // 规范请求期间可继续编辑，响应须通过版本校验
    const target = focusAfterLoad
    focusAfterLoad = null
    if (target === 'isbn' || isNew.value || (isTemp.value && !tempPayload)) isbnInput.value?.focus?.()
    // `addCopy`：从查重弹窗「打开已有条目（加复本）」过来的，直接把光标送到条码框，
    // 编目员落地就能敲号，不用再找。
    //
    // 为什么要**补两枪**：弹窗关闭时 Element Plus 的焦点陷阱会把焦点还给"打开前的
    // 那个元素"，那一下发生在关闭过渡结束之后，正好把我刚设好的光标抢走（实测落到
    // 一个 button 上）。所以先立刻聚焦，过渡结束（约 300ms）后再确认一次。
    else if (route.query.addCopy) focusBarcodeAfterDialog()
    else if (target === 'barcode' || (isTemp.value && route.query.from)) barcodeInput.value?.focus?.()
    else classNoInput.value?.focus?.()
    // 打开记录时的自动规范是界面便利，不是用户改动：结果一旦装进编辑态，
    // 就以规范后的内容重立基线。否则"点进编辑看一眼"立刻被判成"未保存的更改"，
    // 刚保存过的记录也一样（用户 2026-10-24 反馈）。临时底稿不受影响：
    // draftPendingSave 是身份级未保存，重立基线不会让它变干净。
    const normalized = await autoOptimizeAfterLoad(!!tempPayload)
    if (normalized && !tempPayload?.savedBaseline) markSavedBaseline()
  } finally {
    if (current()) loading.value = false
  }
}

// 公共设置加载（优化模式/地点选项/最后条码）
async function loadCommon() {
  await loadOptimizeMode()
  try {
    // 馆藏地点候选来自「字典中心」（内置默认 + 用户增删），改字典即刻反映到这里
    locationOptions.value = (await getDictionary('locations')).items as string[]
  } catch { locationOptions.value = [] }
  try {
    // 文献类型候选来自字典中心（`item_types`）。新字典返回 {code,name}，
    // 旧版这里按字符串直接渲染，接上新库后会拿到对象；统一转成 value/label。
    const rawTypes = (await getDictionary('item_types')).items as Array<string | { code: string; name: string }>
    itemTypes.value = rawTypes.map((item) =>
      typeof item === 'string' ? { value: item, label: item } : { value: item.code, label: item.name })
  } catch { itemTypes.value = [] }
  try {
    // 后端已归一成列表；再兜一层以兼容手工写进库的旧单值 {"digits": 8}
    const raw = (await http.get('/settings/barcode')).data.value?.digits
    const list = digitList(raw ?? 8)
    barcodeDigits.value = list.length ? list : [8]
  } catch { barcodeDigits.value = [8] }
  // 排架号开关（按当前图书库）。读取失败静默降级 false —— 与 optimizeMode 同一风格：
  // 开关只决定"看不看得见"，拿不到就当关，不打断编目。
  try {
    shelfNoEnabled.value = !!(await getShelfNoSetting(currentLibId.value)).enabled
  } catch { shelfNoEnabled.value = false }
  await loadDicts()
  await refreshLastBarcode()
}

/**
 * 简编表单的字典候选：一次取回、本地使用。
 * **不在输入时发请求**——每敲一个字查一次既慢又会把下拉顶出来抢输入。
 * 失败静默降级（候选没了照样能手输）。
 */
async function loadDicts() {
  try {
    languages.value = (await lookupLanguages(undefined, 300)).items ?? []
  } catch { languages.value = [] }
  try {
    commonPlaces.value = (await lookupPlaces(12)).items ?? []
  } catch { commonPlaces.value = [] }
  try {
    // 责任方式候选（`200$f/$g` 下方那排 chip）：来自「字典中心」，改字典即刻反映到这里
    responsibilityOptions.value = (await getDictionary('responsibility')).items as string[]
  } catch { responsibilityOptions.value = [] }
}

/**
 * 本馆默认币种 = 定价框的货币码（「字典中心 → 货币类型」里设）。
 *
 * **必须在灌表单之前拿到**（在 `load()` 开头调用）：定价的显示值要按它剥前缀，
 * 拿晚了会先用兜底 CNY 算一遍，改了币种的馆就会看到「USD | USD12.00」这种重复前缀。
 * 拿不到就留兜底 CNY —— 与 optimizeMode / 排架号开关同一降级风格，不打断编目。
 */
async function loadCurrency() {
  try { currency.value = (await getDefaultCurrency()).code || 'CNY' }
  catch { currency.value = 'CNY' }
}

/**
 * CNMARC 字段字典：字段号 → 中文名 / 子字段占位，只为展示服务（见 composable）。
 */
const { loadMarcFields, fieldName, subfieldName } = useMarcDictionary()

// 注：原 `simpleFromFields(fields, tag, code)` 小工具已随数据源归一**删除**
// （阶段 2/3）——它只在"把 fields 里某个子字段抄进面板状态"时用过，
// 而那 6 处抄写现在全部由 `populateSimple()` 一次完成（simple 就是 fields 的投影）。

/** 排架号开关：写的是**按图书库**的设置（与「图书库列表页」那个开关同一个）。
 *  失败要回滚，不能让界面显示与服务端记录不一致。
 *  胶囊路径走 `toggleShelfNo()`（先翻状态再调本函数），`onShelfNoToggle(v)` 的签名保持不变。 */
async function onShelfNoToggle(v: boolean) {
  try {
    await putShelfNoSetting(currentLibId.value ?? null, !!v)
  } catch (e: any) {
    shelfNoEnabled.value = !v          // 回滚
    ElMessage.error(apiErrorText(e, '保存排架号开关'))
  }
}

function toggleShelfNo() {
  shelfNoEnabled.value = !shelfNoEnabled.value
  onShelfNoToggle(shelfNoEnabled.value)
}

// runOptimize（含 /ai/optimize-local 的 URL 口径与 silent 语义）已抽到
// composables/useOptimize.ts——四重守卫、转圈、改动明细标签全在那边。

// 临时底稿包含记录级信息与待录条码；sessionStorage 回写不等于数据库保存。
function syncTemp() {
  try {
    sessionStorage.setItem('cataloging_temp', JSON.stringify({ fields: currentPayloadFields(),
      leader: currentLeader.value ?? null, ...extraFields(), book_no: extra.bookNo, extra: { ...extra },
      // 书次号是否**手敲过**要跟着底稿走：入库时 `bookNoForSave` 靠它区分「预览回填
      // （不传号，走后端原子取号）」与「用户指定（按手动号抬计数器）」——丢了这个
      // 标志，用户在底稿里手敲的书次号入库时会被静默丢弃。
      book_no_touched: bookNoTouched.value,
      location: location.value, barcode: barcodeInputValue.value, priceDraft: priceDraft.value,
      savedBaseline: savedBaseline.value }))
  } catch { /* 回写失败不阻断 */ }
}

// ===== 书次号（书次号）预览 =====
// 抽到 useBookNoSync：300ms 去抖 + "最新值获胜"序号守卫 + loadedClassNo 值比较抑制。
// 用户一旦手动改过（bookNoTouched），后续分类号变化不再覆盖他填的值。
// loadedClassNo 是组件级的 let（load() / applyCatalogedRecord 都要改它），getter 传入。
// 分类号 / 书次号各自的家已经不同（`simple['690a']` / `extra.bookNo`），
// 故接口收成「读取器 + 写入器」，不再整袋传 `panel`（阶段 1/2）。
const bookNoTouched = ref(false)
let loadedClassNo = ''
// 进入编辑时的书次号快照：分类号没变又把书次号删掉时，回车用它“找回来”。
let loadedBookNo = ''
const { syncBookNo, invalidate: invalidateBookNoSync, dispose: disposeBookNoSync } =
  useBookNoSync({
    classNo: () => tab.value === 'simple' ? simple['690a']
      : currentPayloadFields().find(f => f.tag === '690')?.sf?.find(s => s[0] === 'a')?.[1] ?? '',
    setBookNo: v => { extra.bookNo = v },
    bookNo: () => extra.bookNo,
    currentLibId, recordIdentity: () => id.value,
    bookNoTouched, loadedClassNo: () => loadedClassNo,
  })

/**
 * 分类号回车：归一大小写 + 保证书次号有值。
 *
 * 规则见 `volume.planClassNoEnter`：
 *   - 分类号没变、书次号被删 → 填回进入编辑时的原号；
 *   - 分类号改过 / 本来就没有原号 → 按新分类号强制预览；
 *   - 书次号有值且分类号没变 → 保留，不覆盖手敲值。
 * 强制预览会清掉“手敲过”标记：这次取号是用户显式要求的，后续改分类号应继续自动跟随。
 */
async function onClassNoEnter() {
  const plan = planClassNoEnter({
    input: simple['690a'] ?? '',
    currentBookNo: extra.bookNo ?? '',
    loadedClassNo,
    loadedBookNo,
  })
  simple['690a'] = plan.classNo
  focusNext('bookNo')

  if (plan.action === 'clear') {
    extra.bookNo = ''
    bookNoTouched.value = false
    loadedClassNo = ''
    loadedBookNo = ''
    return
  }

  loadedClassNo = plan.classNo
  if (plan.action === 'restore') {
    extra.bookNo = plan.bookNo
    bookNoTouched.value = false
    loadedBookNo = plan.bookNo
    return
  }
  if (plan.action === 'keep') {
    loadedBookNo = (extra.bookNo || '').trim()
    return
  }

  bookNoTouched.value = false
  const generated = await syncBookNo({ force: true })
  if (generated === null && !(extra.bookNo || '').trim()) {
    ElMessage.error('书次号预览失败，请稍后重试或手动填写')
  }
  loadedBookNo = (extra.bookNo || '').trim()
}

// ===== 就地套录（录入栏的 ISBN 框）=====
// 填 ISBN 回车 → 检索（顺序由后端分层短路：**图书库 → 资源库 → Z39.50**）→
//   命中图书库：同 ISBN 可能是同书的不同卷册/版本 → **弹窗二选一**：
//               打开已有条目（加复本）/ 作为新记录编目（另编一册，见）
//   命中资源库/Z39.50：编新书 → 另存为新记录（见 onCataloged）
// 检索 / 候选弹窗 / "已入库一律另存为新记录"的编排抽到 useCatalogLookup；
// 套录结果的**落点** onCataloged 留在本组件 —— 它要动 fields/simple/panel/extra
// 等全部编辑态（未入库就地填充；已入库另存为新记录，绝不覆盖当前记录）。
const isbnInput = ref()
// 「打开已有条目」主按钮：弹窗打开后聚焦它，只有一条命中时回车即可打开。
// ref 声明留在这里（模板 `ref="candOpenBtn"` 按名绑定），消费在 useCatalogLookup。
const candOpenBtn = ref()
const {
  isbnLookup, looking, candDlg, candList, candInLibrary, candSelected, candFromQuick,
  candHasExternal, candExternalCount, otherBusy, searchOtherSources,
  candPage, candHasMore, gotoCandPage,
  lookupByIsbn, onCandRowClick, onCandOpened, onCandKeydown,
  onCandFocus, confirmCandidate,
  openSelectedLibrary, catalogSelectedAsNew, onCandDialogClosed, resetLookup,
  lookupEnterMuted,
  isIsbnLike, quickBusy, primaryLookup,
} = useCatalogLookup({
  router, isNew, isTemp, recordIdOrNull, currentLibId, barcodeInput, candOpenBtn,
  onCataloged, onQuickRecord,
})

/**
 * 候选弹窗顶部提示。分三种情况说话：
 *   - 只有库内命中：告诉他能"加复本"或"另编一册"；
 *   - 已经手动拉过外部源：说明多出来的那几条可以直接选用套录；
 *   - 从「一键套录」撞进来：补一句同号不同书怎么办。
 */
const candLibraryHint = computed(() => {
  const libraryCount = candList.value.length - candExternalCount.value
  const parts = [`共 ${libraryCount} 条已入库记录命中（可能为同一书目的不同卷册或版本）。`]
  if (candExternalCount.value) {
    parts.push(`另有 ${candExternalCount.value} 条来自资源库 / Z39.50 的著录，选定后即可套录。`)
  }
  parts.push('请选定后决定「加复本」或「另编一册」。')
  if (candFromQuick.value) {
    parts.push('若该记录与已有条目并非同一书目（同号不同书），请点「取消」后直接著录新记录。')
  }
  return parts.join('')
})

/**
 * 「套录」框的唯一入口：一个输入框吃三种输入 —— **ISBN / 题名 / 责任者**。
 * 单按钮按输入分流；Ctrl+Enter 强制走通用候选检索。
 * 检索编排已抽到 useCatalogLookup（lookupByIsbn / primaryLookup）。
 *
 * `library_id`：图书库这一层只看当前书库（同书异校各有一条，不该互相判成"已经编过"）。
 */
function onLookupEnter(event: KeyboardEvent) {
  // 刚在候选弹窗里按回车确认过：弹窗关闭时焦点陷阱会把焦点还给这个输入框，
  // 同一次按键的 keyup 就落到这里，不拦就会立刻再检索一次、弹窗刚关又弹回来。
  // 完整说明见 useCatalogLookup.muteLookupEnter。
  if (lookupEnterMuted()) return
  if (event.ctrlKey || event.metaKey) {
    if (!quickBusy.value) lookupByIsbn()
    return
  }
  primaryLookup()
}

// 从候选弹窗里选定一条（pickCandidate 同在 useCatalogLookup）。

// ===== 套录目标：已入库记录上一律另存为新记录 =====
// 用户实测的故障：在已入库记录页用侧栏「套录」框编下一本书时，表单被换成了新书的数据，
// 但**页面身份仍是旧记录**——填条码回车后走 updateRecord(旧 id) + addHoldings(旧 id)，
// 于是连续几本书的条码全挂到同一条记录下（实测：图书库只剩 1 条记录却挂着 3 个条码，
// 字段内容还是最后一本书的）。
//
// 因此已入库记录上套录**默认另存为新记录**——套录的常态用途就是编下一本新书，
// 直接落到「套录编辑」页（未入库），条码回车即建新记录，绝不碰当前记录。
// 要修改已有记录的字段，直接在简编表单里改，不要靠套录覆盖。
/** 套录结果的唯一入口：未入库就地填充；已入库一律另存为新记录。 */
// ===== 可输入下拉的"失焦即提交"补丁 =====
// ⚠️ `el-select` 的 `filterable` 语义是「**必须点候选**」：打了字但没点候选就失焦，
// Element 会把输入框里的文字**直接丢掉**（`expanded=false` 时清空内部 inputValue）。
// 用户看到的现象就是"填了卷册号 / 装订，保存后没了"——**静默丢数据**。
//
// 修法：在 `blur` 事件里把输入框里还没被确认的文字提交进模型。
// 为什么 `blur` 拿得到值：Element 用 `useEventListener(wrapperRef, 'blur', …, true)`
// 在**捕获阶段**同步转发原生 blur，此刻 DOM 里还留着刚敲的文字；清空发生在随后的
// 微任务（`watch(expanded)`）里，赶不上。
//
// ⚠️ **不要改用 `default-first-option` 来"修"这个问题**：它会把回车变成"选第一条"，
// 悄悄换掉刚敲的值（分类号踩过这个坑，见 README 业务规则）。
// 也**不要**给"选中的就是第一条"之类的字段加它。
function typedSelectValue(e: FocusEvent): string | null {
  const el = e.target as HTMLInputElement | null
  if (!el || el.tagName !== 'INPUT') return null
  const v = (el.value ?? '').trim()
  return v || null
}

/** 卷册号（录入栏 + 简编表单组6 共用；两处绑同一份 `extra.volume`）。 */
function commitVolumeTyped(e: FocusEvent) {
  const v = typedSelectValue(e)
  if (v && v !== (extra.volume ?? '').trim()) extra.volume = v
}

/** 馆藏地点（录入栏；带 default-first-option，回车能提交，但失焦同样会丢）。 */
function commitLocationTyped(e: FocusEvent) {
  const v = typedSelectValue(e)
  if (v && v !== (location.value ?? '').trim()) location.value = v
}

/** 简编表单里带 options 的下拉（装订 `010$b` / 语种 `101$a`）：失焦提交 + 清候选焦点。 */
function onSimpleSelectBlur(b: Binding, e: FocusEvent) {
  focusedKey.value = ''
  const v = typedSelectValue(e)
  const key = b.tag + b.code
  if (!v || v === (simple[key] ?? '').trim()) return
  simple[key] = v
  // 语种要同步"记住上次选择"——用户确实是手打了这个码，算一次真实选择
  if (b.suggest === 'select-language') onLanguageChange(v)
}

/**
 * 输入框下方那排可点候选。**不做下拉**——这些字段编目员心里有值，
 * 下拉会把回车变成"选第一条"，反而把刚敲的值换掉（分类号踩过这个坑）。
 */
function suggestionsFor(b: { suggest?: string }): string[] {
  // 责任方式有 22 条（老系统 `bs_EditType` 全量），按 CHIP_MAX 切片会砍掉大半。
  // 浮层本身是 absolute、不吃布局，配 `.suggest-pop--scroll` 限高滚动即可。
  if (b.suggest === 'responsibility') return responsibilityOptions.value
  const all = ((): string[] => {
    switch (b.suggest) {
      case 'edition': return EDITION_SUGGESTIONS
      case 'size': return SIZE_SUGGESTIONS
      case 'place': return commonPlaces.value
      default: return []        // publisher 走"失焦补出版地"，不铺候选
    }
  })()
  return all.slice(0, CHIP_MAX)   // 见 simpleForm.CHIP_MAX：不限个数会把窄栏撑成多行
}

/**
 * 点候选标签后的落值方式。**两种语义，别混**：
 *   - 一般候选（版次 / 尺寸 / 出版地）→ **替换**：点一下就是整个值；
 *   - 责任方式（`200$f` / `200$g`）→ **追加**：这两栏是「姓名 + 责任方式」的连写
 *     （`张志通主编`），替换会把刚敲的姓名冲掉。追加前先去尾部空白，
 *     免得出现「张志通 著」这种多余空格。
 */
function onSuggestClick(b: Binding, v: string) {
  const key = b.tag + b.code
  if (b.suggest === 'responsibility') {
    const cur = (simple[key] ?? '').trim()
    simple[key] = cur ? `${cur}${v}` : v
    return
  }
  simple[key] = v
}

/**
 * 简编表单里字段的单位前缀。定价（`010$d`）的单位是**本馆默认币种**（可配），
 * 其余字段（页 / cm）是固定单位 —— 与 `simpleForm.unitOf()` 同一口径。
 */
function displayUnit(b: Binding): string {
  return b.tag === '010' && b.code === 'd' ? currency.value : (b.unit ?? '')
}

/**
 * 候选 chip 是否该出现：**仅当该字段正在聚焦时**。
 *
 * 这是"不滚动就能看全表单"的关键一项：原先只要字段为空就铺一行候选，
 * 结果「版次」在 span=6 的窄栏里铺成 **3 行**，把那一行从 38px 撑到 126px
 * （实测：整个表单 792px 里有 88px 是这么来的）。
 * 聚焦才显示 —— 需要候选的时刻正是你准备输入的时刻。
 */
const focusedKey = ref('')
function showSuggest(b: Binding): boolean {
  return focusedKey.value === b.tag + b.code && suggestionsFor(b).length > 0
}

async function onCataloged(record: any, title?: string, focusDelay = 60) {
  if (isNew.value || isTemp.value) {
    if (!await applyCatalogedRecord(record, focusDelay)) return
    ElMessage.success(`已套录《${title ?? '—'}》，请核对内容`)
    await fillPublisherFromIsbn(simple['010a'] ?? '')
  } else {
    await catalogAsNew(record, title)
  }
}

/** 另存为新记录：把套录数据写成"套录底稿"，再把页面身份切成未入库的套录编辑页。 */
async function catalogAsNew(record: any, title?: string) {
  const request = beginReplacement()
  if (!await confirmDiscard() || !replacementIsCurrent(request)) return
  try {
    const prep = (await http.post('/cataloging/prepare',
      { record, agency: 'LOCAL' }).then(r => r.data)).record
    if (!replacementIsCurrent(request)) return
    sessionStorage.setItem('cataloging_temp', JSON.stringify({ ...record, ...prep }))
    approvedNavigation = '/admin/records/temp?from=catalog'
    await router.push(approvedNavigation)
    approvedNavigation = null
    ElMessage.success(`已套录《${title ?? '—'}》为新记录，填条码回车即可入库`)
  } catch (e: any) {
    approvedNavigation = null
    if (replacementIsCurrent(request)) ElMessage.error(apiErrorText(e, '套录'))
  }
}

// 把套录记录落到编辑区：先走 prepare 本地化改写（与套录页同一套管线），
// 再跑一次规则规范拿到最新质检结果，最后同步面板字段。
async function applyCatalogedRecord(record: any, focusDelay = 60) {
  const request = beginReplacement()
  if (!await confirmDiscard() || !replacementIsCurrent(request)) return false
  const prep = (await http.post('/cataloging/prepare',
    { record, agency: 'LOCAL' }).then(r => r.data)).record
  if (!replacementIsCurrent(request)) return false
  const r = optimizeMode.value === 'auto'
    ? await http.post('/ai/optimize-local', { leader: prep.leader, fields: prep.fields }).then(r => r.data)
    : {}
  if (!replacementIsCurrent(request)) return false
  installCatalogDraft({ ...record, ...prep }, r ?? {})
  await nextTick()
  syncBookNo()
  const identity = recordIdentity()
  if (focusDelay > 0) setTimeout(() => {
    if (!disposed && identity === recordIdentity()) barcodeInput.value?.focus?.()
  }, focusDelay)
  return true
}

function installCatalogDraft(record: any, result: any) {
  recordEpoch += 1
  flagDraftPending()
  invalidateReplacements()
  invalidateBookNoSync()
  loadedClassNo = ''
  bookNoTouched.value = false
  tab.value = 'simple' // 先结束旧投影，再安装新稿，避免 tab watch 重建旧数据
  fields.value = JSON.parse(JSON.stringify(result.fields ?? record.fields))
  tempLeader.value = result.leader ?? record.leader ?? null
  // 套录缺省卷册号：/prepare 把 200$h（分册号）放在 record.volume 兜底返回——
  // 同套多卷（囧成语 $h1..6）套录下来 volume 全空，编辑页/列表没有"第几册"可看。
  // record 本身带 volume（库里已有记录/临时稿）时以它为准，只在缺失时用缺省。
  populateExtra({ volume: record.volume ?? (record.extra?.volume ?? undefined), ...record }) // 来源未提供的记录级内容清空，绝不沿用上一本
  findings.value = result.findings ?? []
  optApplied.value = result.applied ?? []
  populateSimple(fields.value)
  if (extra.bookNo) loadedClassNo = (record.search?.class_no ?? simple['690a'] ?? '').trim()
  loadedBookNo = (extra.bookNo || '').trim()
  barcodeInputValue.value = ''
  resetBarcodePreview()
  isbnLookup.value = ''
  resetPrefill()
  if (isTemp.value) syncTemp()
  // 底稿替换是未保存修改，不能把它当成保存成功重置基线。
}

/**
 * 规则优化结果的落点（runOptimize 经 installResult 注入 useOptimize 的回调，
 * 与 installCatalogDraft 同为「整稿替换」写入：findings / fields / leader /
 * simple 一次换新）。守卫与转圈在 useOptimize，这里只管把数据装进编辑态。
 */
function installOptimizeResult(d: any) {
  // 价格框可能装着未提交的算式（如 135/5）：重灌表单前先留住、灌完再放回——
  // 算式绝不能落 MARC（会存成 CNY135/5，见 priceDraft 的注释）。
  const pendingDraft = isPriceExpression(priceDraft.value) ? priceDraft.value : null
  findings.value = d.findings ?? []
  fields.value = JSON.parse(JSON.stringify(d.fields))
  if (d.leader !== undefined) {
    if (detail.value) detail.value.leader = d.leader
    else tempLeader.value = d.leader
  }
  populateSimple(fields.value)
  if (pendingDraft !== null) priceDraft.value = pendingDraft
  // 仅规范界面不代表已落库，不清保存基线；临时底稿把规范结果回写 sessionStorage。
  if (isTemp.value) syncTemp()
}

/**
 * 「一键套录」的落点（`POST /api/quick-entry` 的 `status === 'ok'`）。
 *
 * **为什么不能复用 `onCataloged`**：快速录入的响应已经是本地化后的记录
 * （001 重编、801 改写、9xx 剥离、分类号截取、规则补全都做完了）。而 `onCataloged`
 * 会再走一次 `/cataloging/prepare` → `localize_record`，那个函数**不幂等**：
 * 会把 001 再编一遍、再往 035 里塞一条，等于把刚拿到的干净记录改坏。
 * 所以这里只跑幂等的规则层优化与质检，不碰 prepare。
 *
 * 落点规则与套录一致：未入库页（新建 / 临时副本）就地填充；已入库页另存为
 * 临时副本再跳过去 —— 绝不覆盖当前记录。
 */
async function onQuickRecord(record: any, hitSource?: string) {
  const request = beginReplacement()
  if (!await confirmDiscard() || !replacementIsCurrent(request)) return
  if (!isNew.value && !isTemp.value) {
    sessionStorage.setItem('cataloging_temp', JSON.stringify(record))
    approvedNavigation = '/admin/records/temp?from=quick'
    try { await router.push(approvedNavigation) } finally { approvedNavigation = null }
    ElMessage.success(`已取到${hitSource ? `（${hitSource}）` : ''}套录数据，填条码回车即可入库`)
    return
  }
  const r = optimizeMode.value === 'auto'
    ? await http.post('/ai/optimize-local', { leader: record.leader, fields: record.fields }).then(r => r.data)
    : {}
  if (!replacementIsCurrent(request)) return
  installCatalogDraft(record, r ?? {})
  await nextTick()
  syncBookNo()
  ElMessage.success(`已取到${hitSource ? `（${hitSource}）` : ''}套录数据，请核对后贴条码入库`)
  const identity = recordIdentity()
  setTimeout(() => {
    if (!disposed && identity === recordIdentity()) barcodeInput.value?.focus?.()
  }, 60)
}

// ===== 录入栏方向键导航 + 回车跳格链 =====
// 抽到 usePanelNav：PANEL_ROWS 行表（↑/↓ 一行一站，A 行三站合一）与六个输入框
// ref 的绑定收敛在同源一处。排架号刻意不在表里——随开关显隐，且不打断"条码回车即入库"。
const { onPanelArrow, focusNext } = usePanelNav({
  isbnInput, classNoInput, bookNoInput, volumeInput, priceInput, barcodeInput,
})

// ===== 简编表单键盘导航 =====
// 抽到 useSimpleKeyNav：行结构 simpleFormRows() 与模板栅格同源（simpleForm.ts 唯一真源），
// "不抢"守卫（el-select 的 Enter/↑↓、textarea 换行、←/→ 贴边）都在那边。
const { onSimpleKeydown } = useSimpleKeyNav({ shelfNoEnabled })

// ===== 价格框：简单计算器 =====
// 框体（含「焦点进入即全选 + 拦住紧随其后的那一次 mouseup」）已整块搬进
// components/PriceInput.vue（阶段 4a）——那是这个框自己的 DOM 行为，
// 留在页面反而要把它拆成 focus / mousedown / mouseup 三个 emit。
// 数据侧（草稿 / 结果 / 失败标志）仍在本页；本页只留 `priceInput` 这个 ref：
// ↑/↓ 导航与回车跳格链（usePanelNav）要送光标进去，组件 `defineExpose({ focus })`
// 与 `el-input` 一样按 `.focus()` 调用。
//
// 整值替换之外，支持直接敲算式（如 `135/5` = 整套总价 ÷ 册数 = 单本价）。
// 求值纯逻辑在 src/priceExpr.ts（自写 tokenizer + 递归下降，**不用 eval**，有单测）。
//
// `priceResult`：算式且求值成功时显示的浮层文本（`= 27.00`）；失败 / 非算式 → 空串（浮层不显示）。
const priceResult = computed(() => {
  const v = priceDraft.value ?? ''
  if (!isPriceExpression(v)) return ''
  const n = evalPriceExpression(v)
  return n === null ? '' : `= ${formatPrice(n)}`
})
// 提交失败的短提示（框下方一行，1.5s 自动消失）
const priceCommitError = ref(false)
let priceErrorTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 把价格框里的算式求值并落成金额。**只处理算式**：纯数字 / 空串原样保留，
 * 不做"45 → 45.00"这类重格式化（那会改变用户输入）。
 * 失败（非法 / 除零）时不改值，只给一行短提示（1.5s 后自动消失）。
 */
function commitPrice() {
  const raw = (priceDraft.value ?? '').trim()
  if (!isPriceExpression(raw)) return
  const n = evalPriceExpression(raw)
  if (n === null) {
    priceCommitError.value = true
    if (priceErrorTimer) clearTimeout(priceErrorTimer)
    priceErrorTimer = setTimeout(() => { priceCommitError.value = false }, 1500)
    return
  }
  if (priceErrorTimer) { clearTimeout(priceErrorTimer); priceErrorTimer = null }
  priceCommitError.value = false
  priceDraft.value = formatPrice(n)
}

/** 回车：先求值，再保持原有的跳格到条码框（主链路「条码回车即入库」不变）。 */
function onPriceEnter() {
  commitPrice()
  focusNext('barcode')
}

// 只有「馆藏地点」这个 el-select 需要走全局监听：Element Plus 的下拉会先把焦点
// 收回去，所以要延后一点再送。其余都是普通输入框，各自挂 @keyup.enter 就行。
// （原实现用 t.closest('.el-select') 一刀切，把馆藏地点的回车也送去了价格框、
//   跳过了 ISBN；分类号改成纯输入后这个选择器更没意义了。）
function onPanelKeyup(e: KeyboardEvent) {
  if (e.key !== 'Enter') return
  const t = e.target as HTMLElement
  if (!(t instanceof Element)) return
  if (t.closest('[data-jump="isbn"]')) {
    setTimeout(() => focusNext('isbn'), 250)
  }
}

// ===== 质检未通过（422）的处理出路 =====
// 状态机（gateDlg / openGate / forceSave / optimizeFromGate）在 useQualityGate，
// 顶部解构；背景：之前只弹一句 toast 提示"可点优化"，但套录副本（isNew）下
// 优化要求先入库，形成"保存失败→点优化→请先保存"的死循环，用户彻底卡死。

// ===== 保存并生成馆藏 =====
// fromBarcode：本次保存是不是「条码框回车」触发的。是的话存完按「套录模式」决定落点 ——
//   套录模式：光标回 ISBN 框，形成"编目一本接一本"的闭环（ISBN → 套录 → 条码回车 → 保存 → 回 ISBN）；
//   非套录模式：留在条码框，给同一条记录连续贴多册（保存时条码框已被清空）。
// （用显式参数而不是"总是移焦点"：点保存按钮时不该抢用户的焦点。）
async function saveAndHoldings(fromBarcode = false) {
  if (saving.value) return   // 重入守卫：扫码枪连发 / 双回车时第一次请求还在途，
                             // 重复请求会生成重复馆藏或撞条码唯一约束（:loading 只拦鼠标）
  if (isNew.value || isTemp.value) return saveNew()      // 未入库记录走创建路径
  saving.value = true
  invalidateReplacements()
  const identity = recordIdentity()
  const version = editVersion.value
  try {
    const payloadFields = currentPayloadFields()
    let d
    try {
      d = await updateRecord(recordIdOrNull.value!, {
        fields: payloadFields, leader: currentLeader.value, force: forcing.value,
        book_no: bookNoForSave({ creating: isNew.value || isTemp.value,
                                 touched: bookNoTouched.value, value: extra.bookNo }),
        ...extraFields(),                            // 卷册号 / 排架号（种级）
        expected_updated_at: detail.value?.updated_at,   // 乐观锁：期间被别人改过就 409
      })
    } catch (e: any) {
      if (disposed || identity !== recordIdentity()) return
      // 错误经 lib/api.ts 统一包装成 ApiError：status 在 e.status、原始响应体在 e.raw
      if (e?.status === 422 && !forcing.value) {   // 质检门槛
        openGate(e.raw?.detail?.errors ?? [],
                 async () => { forcing.value = true; try { await saveAndHoldings() } finally { forcing.value = false } })
        return
      }
      throw e
    }
    if (disposed || identity !== recordIdentity()) return
    // 保存期间继续输入时不冲掉新输入，也不清脏。
    const unchanged = version === editVersion.value
    const pendingDraft = isPriceExpression(priceDraft.value) ? priceDraft.value : null
    let savedPriceDraft = priceDraft.value
    if (unchanged) {
      fields.value = JSON.parse(JSON.stringify(d.fields))
      detail.value = { ...detail.value, ...d }
      findings.value = d.findings ?? []
      populateSimple(fields.value)
      savedPriceDraft = priceDraft.value
      if (pendingDraft !== null) priceDraft.value = pendingDraft
    }
    const expected = JSON.parse(editSnapshot())
    const added = unchanged ? await saveHoldingsOnly() : null
    if (added && added > 0) expected.barcode = ''
    if (unchanged && added !== null && identity === recordIdentity()
        && editSnapshot() === JSON.stringify(expected)) {
      expected.priceDraft = savedPriceDraft
      commitBaseline(expected)
    }
    rememberShelfNo()                 // 记下这次用的架位，供下一条新记录预填
    // added === null：条码填了但不可用（位数不符等），上面已弹错误提示；
    // 这里不再报"已保存"，免得把"条码没入库"这件事盖过去。
    if (added !== null) ElMessage.success(added ? `已保存并生成 ${added} 册馆藏` : '已保存')
    // 条码回车保存后的落点由「套录模式」决定：
    //   套录模式 → 回 ISBN 框（编下一本）；关掉 → 留在条码框（继续贴下一册）
    if (fromBarcode) {
      if (catalogMode.value === 'catalog') isbnInput.value?.focus?.()
      else barcodeInput.value?.focus?.()
    }
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '保存'))
  } finally { saving.value = false }
}

// 临时副本/新建保存：POST /records（质检门槛 + 条码门槛）→ 转正式编辑。
// 条码与记录一次写入：不再"先建记录再补馆藏"，从根上杜绝无条码记录。
async function saveNew() {
  if (saving.value) return               // 重入守卫（同 saveAndHoldings）
  const payloadFields = currentPayloadFields()
  const barcodes = collectBarcodes()
  if (!barcodes) return                    // 未填条码：已提示，中止
  saving.value = true
  invalidateReplacements()
  const identity = recordIdentity()
  const version = editVersion.value
  const submitted = JSON.parse(editSnapshot())
  try {
    let d
    try {
      d = await createRecord({ fields: payloadFields, force: forcing.value,
                               leader: currentLeader.value, barcodes,
                               location: location.value || undefined,
                               book_no: bookNoForSave({
                                 creating: isNew.value || isTemp.value,
                                 touched: bookNoTouched.value, value: extra.bookNo }),
                               library_id: currentLibId.value,
                               ...extraFields() })
    } catch (e: any) {
      if (disposed || identity !== recordIdentity()) return
      // 错误经 lib/api.ts 统一包装成 ApiError：status 在 e.status、原始响应体在 e.raw
      if (e?.status === 422 && !forcing.value) {
        openGate(e.raw?.detail?.errors ?? [],
                 async () => { forcing.value = true; try { await saveNew() } finally { forcing.value = false } })
        return
      }
      throw e
    }
    if (disposed || identity !== recordIdentity()) return
    const unchanged = version === editVersion.value && !isPriceExpression(priceDraft.value)
    if (barcodeInputValue.value === submitted.barcode) barcodeInputValue.value = ''
    if (unchanged) {
      fields.value = JSON.parse(JSON.stringify(d.fields))
      tempLeader.value = d.leader ?? tempLeader.value
      populateSimple(fields.value)
      markSavedBaseline()
    } else {
      // 新记录已创建：保留在途的新输入转入该记录，不能再留在 new 身份重复创建。
      submitted.fields = d.fields
      submitted.leader = d.leader ?? submitted.leader
      submitted.barcode = ''
      submitted.priceDraft = stripUnit((d.fields as MarcField[]).find(f => f.tag === '010')
        ?.sf?.find(s => s[0] === 'd')?.[1] ?? '', currency.value, 'prepend').trim()
      commitBaseline(submitted)
      detail.value = d
      savedNavigation = String(d.id)
    }
    flagDraftPending(false)
    sessionStorage.removeItem('cataloging_temp')
    rememberShelfNo()
    ElMessage.success(`已编目完成，记录已入图书库（${barcodes.length} 册）`)
    focusAfterLoad = catalogMode.value === 'catalog' ? 'isbn' : 'barcode'
    const target = `/admin/records/${d.id}/edit`
    approvedNavigation = target // 保存引起的身份切换不再次询问；在途输入由 savedNavigation 保留
    try { await router.replace(target) } finally { approvedNavigation = null }
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '保存'))
  } finally { saving.value = false }
}

// 只有条码框有内容时才生成馆藏，返回新增册数（0 = 未生成）。
// 原先条码框为空会默认加 1 册，导致"只改字段点保存"也凭空多出一册。
// 返回新增册数；0 = 条码框为空（只存字段）；**null = 填了但不可用（已给出提示）**。
// 填了却不可用时**必须出声**——原先是静默 return 0，用户会以为条码已经入库。
async function saveHoldingsOnly(): Promise<number | null> {
  if (!barcodeInputValue.value.trim()) return 0
  const identity = recordIdentity()
  const rid = recordIdOrNull.value!
  const barcode = barcodeInputValue.value
  const parsed = parseBarcodeInput(barcode)
  if (!parsed.codes.length) {
    ElMessage.error(parsed.error && parsed.error !== 'empty'
      ? parsed.error : '条码无法识别，未生成馆藏')
    barcodeInput.value?.focus?.()
    return null
  }
  try {
    const h = await addHoldings(rid, parsed.codes.length,
                                location.value || undefined, undefined, parsed.codes)
    const list = await listHoldings(rid)
    if (disposed || identity !== recordIdentity()) return null
    holdings.value = list.holdings
    await refreshLastBarcode()
    if (identity !== recordIdentity()) return null
    if (barcodeInputValue.value === barcode) barcodeInputValue.value = ''
    return h.created
  } catch (e: any) {
    if (identity === recordIdentity()) ElMessage.warning(`记录已保存，馆藏生成失败：${apiErrorText(e, '生成馆藏')}`)
    return null
  }
}

// ===== 馆藏行编辑/删除/批量 =====
/** 只重载馆藏列表（把被改动的输入框恢复成库里的值）。**不要**用整页 load()——
 *  那会把录入面板里还没保存的分类号/书次号/价格一起冲掉。 */
async function reloadHoldings() {
  try { holdings.value = (await listHoldings(recordIdOrNull.value!)).holdings } catch { /* 忽略 */ }
}

// 表格里确认条码/地点时，除了落库这一行，还要同步保存记录本身——
// 否则在录入面板改过的分类号、书次号、价格会丢（用户报过这个）。
async function saveHolding(row: any) {
  invalidateReplacements()
  const identity = recordIdentity()
  const version = editVersion.value
  // 条码位数先过一遍前端（与后端同一套规则）：不符就别发请求了，
  // 否则等服务端 400 回来时输入框里已经是一个存不进去的值。
  const bc = (row.barcode ?? '').trim()
  if (!bc) {
    ElMessage.error('条码不能为空')
    await reloadHoldings()
    return
  }
  if (!barcodeDigitsAllowed(bc, barcodeDigits.value)) {
    ElMessage.error(`条码 ${bc} 为 ${barcodeWidth(bc)} 位，不在设置的条码位数`
                    + `（${barcodeDigits.value.join(' / ')} 位）内，未保存`)
    await reloadHoldings()
    return
  }
  try {
    // 1) 先落库本行（用户的直接动作，优先保证）
    await http.put(`/holdings/${row.id}`, { barcode: row.barcode, location: row.location })
    // 2) 同步保存记录字段；未入库的记录（新建/临时副本）由保存按钮统一处理
    if (!isNew.value && !isTemp.value) {
      try {
        if (disposed || identity !== recordIdentity() || version !== editVersion.value) return
        const d = await updateRecord(recordIdOrNull.value!, {
          fields: currentPayloadFields(), leader: currentLeader.value,
          book_no: bookNoForSave({ creating: isNew.value || isTemp.value,
                                   touched: bookNoTouched.value, value: extra.bookNo }),
          // 卷册号 / 排架号也要带上：否则"改条码后卷册号被当成未提供"语义不一致（规则 13）
          ...extraFields(),
          expected_updated_at: detail.value?.updated_at,   // 乐观锁，同 saveAndHoldings
        })
        if (disposed || identity !== recordIdentity() || version !== editVersion.value) return
        const pendingDraft = isPriceExpression(priceDraft.value) ? priceDraft.value : null
        fields.value = JSON.parse(JSON.stringify(d.fields))
        detail.value = { ...detail.value, ...d }
        findings.value = d.findings ?? []
        populateSimple(fields.value)
        // 行编辑不动录入面板的条码框：重立基线须保住旧基线的 barcode（见 composable）
        preserveOldBarcode()
        if (pendingDraft !== null) priceDraft.value = pendingDraft
      } catch (e: any) {
        ElMessage.warning(`条码已保存，但记录字段未保存：${apiErrorText(e, '保存')}`)
        return
      }
    }
    holdings.value = (await listHoldings(recordIdOrNull.value!)).holdings
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '保存'))
    await reloadHoldings()
  }
}
/**
 * 删除单册馆藏。删除**不可逆**——若只是剔旧 / 损毁 / 遗失，应改用「条码管理」页的
 * 「注销」（留账、可恢复），故确认文案要点明后果。
 *
 * 确认框与真正的删除**各起一层 try/catch**：取消时静默 return（不当失败处理），
 * 删除失败必须弹错误提示——不能让馆员以为"点了没反应"。
 */
async function delHolding(row: any) {
  // 最后一条条码：后端明确拒绝单删（holdings.py::delete_holding →
  // 「图书库要求每条记录至少保留一个条码；如需移除请直接删除该记录」），
  // 否则会留下"有条目无条码"的记录。与其让用户撞一个看不懂的 400，
  // 这里就地接到「删除整条记录」上：要移除这一册，只能连记录一起删。
  // 判定口径与后端一致 —— 数的是该记录下**全部** holdings（含已注销/已遗失）。
  // 严格 `=== 1`：列表为空（尚未加载 / 取数失败）时**不能**判定成"最后一条"，
  // 那会把人引向"删除整条记录"，比误删一册严重得多 —— 这种情况按老路径走，
  // 让后端去做权威判定（它会拒绝并给出原因）。
  if (holdings.value.length === 1) {
    try {
      await ElMessageBox.confirm(
        `这是该记录的最后一条条码（${row.barcode ?? row.id}）。\n\n` +
        `图书库要求每条记录至少保留一个条码，不能单独删除这一册，只能连同记录一起删除。\n\n` +
        `删除记录会一并删除该书目及其全部条码，不可恢复。`,
        '这是最后一条条码', {
          type: 'warning',
          confirmButtonText: '删除整条记录', cancelButtonText: '取消',
        })
    } catch { return }      // 用户取消
    try {
      await deleteRecord(recordIdOrNull.value!)
      ElMessage.success('已删除该记录')
      router.push('/admin/records')
    } catch (e: any) {
      ElMessage.error(apiErrorText(e, '删除记录'))
    }
    return
  }

  try {
    await ElMessageBox.confirm(
      `确定删除条码 ${row.barcode ?? row.id}？删除不可恢复；` +
      `如果只是剔旧或损毁，请改用「条码管理」页的「注销」。`,
      '删除馆藏', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  try {
    await deleteHolding(row.id)
    holdings.value = (await listHoldings(recordIdOrNull.value!)).holdings
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(apiErrorText(e, '删除'))
  }
}

// 增删字段 / 子字段的实现已搬进 components/MarcPane.vue——
// 它对传入的 `fields` 原地 push/splice，改的是同一份响应式数组。

// 录入面板回车跳格：全局 keyup 监听，只在 target 落在录入卡片的「套录」框（`data-jump="isbn"`）时才接管
let panelKeyupHandler: ((e: KeyboardEvent) => void) | null = null

onMounted(() => {
  window.addEventListener('beforeunload', onBeforeUnload)
  if (!panelKeyupHandler) {
    panelKeyupHandler = onPanelKeyup
    window.addEventListener('keyup', panelKeyupHandler)
  }
})
onUnmounted(() => {
  disposed = true
  loadSeq += 1
  invalidateReplacements()
  window.removeEventListener('beforeunload', onBeforeUnload)
  if (panelKeyupHandler) {
    window.removeEventListener('keyup', panelKeyupHandler)
    panelKeyupHandler = null
  }
  // 价格算式的失败提示定时器：组件卸载时清掉，避免在已销毁的实例上 setState
  if (priceErrorTimer) {
    clearTimeout(priceErrorTimer)
    priceErrorTimer = null
  }
  // 各 composable 里挂起的去抖定时器一并清掉（watch 随 setup 自动停止，setTimeout 不会）
  disposeBarcodePreview()
  disposeBookNoSync()
})

onMounted(load)

// 同一路由 /records/:id 的参数变化时组件会被复用（不重新 mount）。
// 保存后 router.replace 到新记录、或在临时副本与正式记录之间切换时，
// 必须在这里手动重新加载，否则界面一直停留在旧数据（"保存成功但没显示出来"）。
watch(id, (nv, ov) => {
  if (nv === ov) return
  invalidateReplacements()
  invalidateBookNoSync()
  if (savedNavigation === nv) {
    savedNavigation = null
    recordEpoch += 1
    const target = focusAfterLoad
    focusAfterLoad = null
    reloadHoldings()
    refreshLastBarcode()
    nextTick(() => {
      if (target === 'isbn') isbnInput.value?.focus?.()
      else barcodeInput.value?.focus?.()
    })
    return
  }
  load()
}, { flush: 'sync' })
</script>

<style scoped>
/* CNMARC 字段中文名（「全字段 MARC」里挂在字段号标签旁，灰字不抢眼、只是提示） */
/* 整体撑满视口：el-main 上下 padding 各 16px，故减去 32px */
.editor-card {
  display: flex; flex-direction: column;
  min-height: calc(100vh - 32px); box-sizing: border-box;
}
.editor-card :deep(.el-card__body) {
  flex: 1; display: flex; flex-direction: column; min-height: 0;
  /* 默认 20px 太大；表单要"不滚动看全"，上下各让 6px */
  padding: 14px 16px;
}
/* tab 标题行与内容之间的默认 15px 也收一收 */
.editor-card :deep(.el-tabs__header) { margin-bottom: 8px; }
.editor-row { flex: 1; min-height: 0; }
/* 顶部工具条与内联质检摘要的样式随 components/EditorToolbar.vue 一起维护。 */

/* 侧栏：录入卡片（内含馆藏条码栏）高度由 JS 与左栏表单底部对齐，
   内容多时由 min-height 自然撑开，不会裁掉馆藏列表 */
.side-col { display: flex; flex-direction: column; gap: 12px; }
.side-col .entry-panel { flex: 0 0 auto; }
/* 录入栏行距收紧（12 → 8px）。**只改间距，不动 data-field / ref / 跳格链** ——
   「ISBN 回车套录 → 条码回车入库」这三步与 ↑/↓ 行移动、回车跳格链是红线。 */
.entry-panel :deep(.el-form-item) { margin-bottom: 8px; }
/* 录入栏分区细线：馆藏信息 / 套录 / 索书号 / 价格条码，少一层脑内分组 */
.panel-sep { height: 1px; background: var(--primary-soft); margin: 0 0 8px; }

/* 占位提示更淡（EP 默认 --el-text-color-placeholder 在表单里偏显眼）。
   用主题色按比例混到面板底色上得到同样的淡度：#8a9099@72% over #fff ≈ #c4c8d0，
   但换成 var(--app-text-muted) 才能跟着暗色模式走（写死 #c4c8d0 暗下发虚）。 */
.simple-form, .entry-panel {
  --el-text-color-placeholder: color-mix(in srgb, var(--app-text-muted) 72%, var(--app-surface));
}
/* 注：作业方式胶囊栏（.mode-bar/.mode-chip*）与条码预览行（.preview-line/.conflict-line）
   的样式已随模板搬进 components/ModeBar.vue 与 components/BarcodePreviewLine.vue
   （scoped 样式不跨组件生效，各带各的） 阶段 4a。 */
/* 套录候选条目：整行可点，悬停高亮 */
.cand-item {
  border: 1px solid var(--app-border); border-radius: 6px; padding: 10px 12px;
  margin-bottom: 8px; cursor: pointer;
}
.cand-item:hover { border-color: var(--el-color-primary); background: color-mix(in srgb, var(--app-primary) 5%, var(--app-surface)); }
/* 键盘走到哪一条要看得出来。用 :focus-visible —— 鼠标点出来的焦点不画圈，
   免得点一下满屏都是框。 */
.cand-item:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
/* 图书库候选的选中态（点行选中，再用底部按钮决定"打开已有"还是"另编一册"） */
.cand-item--on { border-color: var(--el-color-primary); background: var(--app-menu-active-bg); box-shadow: 0 0 0 1px var(--el-color-primary) inset; }
/* 「库里已有」时的扩展检索入口：做成带边框的动作区，而不是行内链接——
   链接样式太弱，新编目员看不出那里能点。 */
/* 说明文字靠左、按钮靠右：按钮是动作，落在右下角光标自然停留的位置。
   用 `space-between` 而不是给按钮加 `margin-left:auto`——后者要穿透 ElButton 的
   内部结构（scoped 样式选不中），换 DOM 顺序 + 容器对齐更稳，也不依赖子组件实现。 */
.cand-more {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin: -2px 0 12px;
  padding: 10px 12px;
  border: 1px dashed var(--app-border);
  border-radius: 8px;
}
.cand-more-hint { font-size: 12.5px; color: var(--app-text-muted); flex: 1 1 260px; }
/* 候选分页：靠右，与底部按钮同侧 */
.cand-pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 10px;
  font-size: 12.5px;
  color: var(--app-text-muted);
}
.cand-meta {
  display: flex; gap: 16px; flex-wrap: wrap;
  font-size: 12px; color: var(--app-text-muted); margin-top: 6px;
}
.unit-fixed { color: var(--app-text-muted); user-select: none; }

/* ── 索书号一行（分类号 / 书次号 / 卷册号）─────────────────────────────
   `/` 独立成窄元素（不再用 el-input 的 `#prepend`：那个 addon 自带 padding，
   实测吃掉约 30px，把书次号挤得显示不全）。
   宽度用 `flex: 0 1 <理想宽>`：**宽屏保持 90/70/92 的理想版式（不 grow）**，
   **窄屏按比例收缩**——录入栏 label-width=70px + 卡片 padding 之后，el-form-item__content
   的实际可用宽只有 ~226px（1440）/ ~210px（1366）；字号已由 14px 收到 12px（见下方 :deep 规则），
   理想宽相应从 90/76/100 收到 90/70/92，`/` 两侧更紧凑。三项为 `0 1` + `min-width:0`，
   理想总宽（≈278，含分隔符/gap）超出可用宽时**按比例收缩**，绝不横向溢出。 */
.callno-row { display: flex; gap: 6px; align-items: center; width: 100%; }
.callno-row .callno-cls { flex: 0 1 90px; min-width: 0; }
.callno-row .callno-sep {
  flex: 0 0 auto; padding: 0; color: var(--app-text-muted);
  font-family: Consolas, Menlo, monospace; font-size: 12px; line-height: 1;
}
.callno-row .callno-no  { flex: 0 1 70px; min-width: 0; }
.callno-row .callno-vol { flex: 0 1 92px; min-width: 0; }
/* 字号微调：分类号行三控件字号 14px → 12px（用户："适当缩小字体"）。
   作用域限定在 .callno-row，不影响录入栏其它字段。
   实现方式：**直接覆盖子组件内部元素的 font-size**（`el-input` 的 `.el-input__inner` 是
   `font-size:inherit`（继承 `.el-input` 的 `--el-font-size-base`），而 `el-select` 的
   `.el-select__wrapper` 把 `font-size:14px` **写死**（既不读变量也不继承），两条路径不同，
   故两者都显式覆盖；`:deep()` 用于穿透 scoped 样式到子组件内部 DOM。
   占位灰字（书次号 / 卷册号）随 wrapper 继承同缩为 12px。行高仍由 --el-component-size
   控制（不变），只是文字整体变小。 */
.callno-row :deep(.el-input__inner),
.callno-row :deep(.el-select__wrapper) { font-size: 12px; }

/* 简编表单组6 用的是**同一个** `.callno-row`（与录入栏同构），但可用宽度差一倍：
   录入栏侧栏只有 ~226px，三格必须按理想宽收缩（`0 1`）；简编表单这一行有 12~14 格
   （≈350~470px），`0 1` 会留下大片空白 —— 这里改 `1 1`，让三格**按比例铺满**。
   分类号略宽（分类号 `G624.203` 比书次号长），书次号最窄。 */
.simple-form .callno-row .callno-cls { flex: 1 1 90px; }
.simple-form .callno-row .callno-no  { flex: 1 1 70px; }
.simple-form .callno-row .callno-vol { flex: 1 1 92px; }

/* 注：价格计算器的结果浮层 `.price-result` / 失败提示 `.price-error` 的样式
   已随框体搬进 components/PriceInput.vue（scoped 样式不跨组件生效，各带各的），
 阶段 4a。`.unit-fixed` 与 `.suggest-line` 本页仍有使用者（简编表单
   的单位后缀、排架号警告行），故留在下面。 */

/* ── 排架号框尾的 ± 调整键（录入栏）────────────────────────────────────
   侧栏 `el-form-item__content` 只有 ~156px(1440) / ~140px(1366)，**还要扣掉 70px 标签宽**
   （§5 记过这条），所以两个键必须压到最窄：20px、无 padding、无边框。
   `el-input-group__append` 自带 padding，只在这一处抹掉（`.shelfno-input` 限定作用域，
   否则会连带改掉「套录」框那个 append 搜索按钮）。 */
.shelfno-input :deep(.el-input-group__append) { padding: 0 4px; }
.shelfno-step { display: inline-flex; align-items: center; gap: 2px; }
.shelfno-step__btn {
  width: 20px; height: 20px; padding: 0; border: 0; background: transparent;
  cursor: pointer; font-size: 14px; line-height: 1; color: var(--app-text);
}
.shelfno-step__btn:hover:not(:disabled) { color: var(--el-color-primary); }
.shelfno-step__btn:disabled { color: var(--el-text-color-placeholder); cursor: not-allowed; }

/* 候选：输入框下方一排可点标签。不做下拉，避免抢输入。
   **浮层而非块级**：只在聚焦时出现，且不参与布局 —— 块级渲染在 span=6 的窄栏里会折成
   3 行（实测 62px），把下方字段整体推下去，直接破坏"不滚动看全"。 */
.simple-form :deep(.el-form-item__content) { position: relative; }
.suggest-pop {
  position: absolute; top: calc(100% + 2px); left: 0; z-index: 20;
  display: flex; flex-wrap: wrap; gap: 4px; line-height: 1;
  padding: 5px 6px; max-width: 360px;
  background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 4px;
  box-shadow: 0 3px 12px rgba(0, 0, 0, 0.1);
}
/* 责任方式候选（22 条）**限高滚动**而不是切片：切片按字典顺序砍，
   会把「著 / 编 / 译」这些最常用的挡在外面。浮层是 absolute、不参与布局，
   限高不会把表单撑高。 */
.suggest-pop--scroll { max-height: 132px; overflow-y: auto; max-width: 320px; }
/* 警告/提示用的行内小字（与候选浮层区分开，保持原来的占位行为） */
.suggest-line {
  display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; line-height: 1;
}
.suggest-chip {
  font-size: 12px; color: var(--app-text); cursor: pointer;
  padding: 2px 6px; border: 1px solid var(--app-border); border-radius: 3px; background: var(--app-surface-2);
}
.suggest-chip:hover { color: var(--el-color-primary); border-color: var(--el-color-primary); background: var(--app-menu-active-bg); }

/* ===== 简编表单：分区 / 必备星标 / 数值右对齐 / 只读检索点=====
   全部样式服务同一个目标：**不滚动就能看全表单**。所以组间距、行间距都刻意收紧，
   并且**没有任何一条常驻的说明行**——提示走 placeholder，候选走"空值/聚焦才出现"。 */
/* 页面顶部工具条（`.editor-head`）的样式已随组件搬进 components/EditorToolbar.vue */
/* 分区标题：小号灰字 + 左侧色条；组间距 4/1px（原 18/10px → 6/2px → 2026-10-25
   用户反馈 1080P 100% 缩放（视口 ≈945px，落在下方两个紧凑媒体查询之外）仍出
   滚动条，基础档再收一档）。8 个组 × 每档省 3px ≈ 24px。 */
.group-head { display: flex; align-items: center; gap: 6px; margin: 4px 0 1px; }
.group-head:first-child { margin-top: 0; }
.group-bar { width: 3px; height: 12px; border-radius: 2px; background: var(--el-color-primary); flex: 0 0 auto; }
.group-title { font-size: 12px; font-weight: 700; letter-spacing: .5px; color: var(--app-text); }
.group-legend { font-size: 11px; color: var(--app-text-muted); }
/* 必备字段红星 */
.req-star { color: var(--el-color-danger); font-weight: 700; margin-right: 2px; }
/* 数值右对齐 + 等宽数字（对标老系统字段定义文件的 MiddleRight） */
.num-input :deep(input) { text-align: right; font-variant-numeric: tabular-nums; }
/* 简编表单：标签字号收小以适配 6/8 栅格的窄栏；行间距 4px（Element 默认 18px）。
   `align-items: center` 是必需的：Element 的标签是 `display:inline-flex; align-items:flex-start`，
   而 `*` 是 flex 子项，会被顶到控件行高的**行首**，看起来像飘在文字上方（实测截图确认）。
   控件高度基础档 28px（Element 默认 32）：与 4px 行距一起，11 行表单 + 8 个组头
   合计省下约 90px——正好是 1080P 100% 缩放时溢出的那一截。标签 line-height 必须
   跟着控件走（默认 32px 会把行高钉死在 32，控件再矮也不生效，见下方媒体查询同款做法）。 */
.simple-form { --el-component-size: 28px; }
.simple-form :deep(.el-input__wrapper),
.simple-form :deep(.el-select__wrapper) {
  min-height: 28px; padding-top: 1px; padding-bottom: 1px;
}
.simple-form :deep(.el-form-item) { margin-bottom: 4px; }
.simple-form :deep(.el-form-item__label) {
  font-size: 13px; padding-right: 8px; align-items: center; line-height: 28px;
}

/* ── 矮屏自适应（1366×768 这类学校常见机器）─────────────────────────────
   基础档已收紧行距（控件 28px / 行距 4px，见上），1080P 100% 缩放零滚动；
   本档与下一档仍保留给更矮的屏（1536×864、1366×768），把控件压成紧凑尺寸
   （Element 的 --el-component-size 就是输入框/下拉的高度来源）并再收行距。
   只作用于 .simple-form 与 .editor-card，不影响右侧录入面板与其它页面。 */
@media (max-height: 890px) {
  .simple-form { --el-component-size: 24px; }
  .simple-form :deep(.el-input__wrapper),
  .simple-form :deep(.el-select__wrapper) {
    min-height: 24px; padding-top: 1px; padding-bottom: 1px;
  }
  .simple-form :deep(.el-form-item) { margin-bottom: 3px; }
  .simple-form :deep(.el-form-item__label) { line-height: 24px; }
  .simple-form :deep(.el-textarea__inner) { min-height: 38px; height: 38px; }
  .group-head { margin: 3px 0 1px; }
  .editor-card :deep(.el-card__body) { padding: 10px 14px; }
  .editor-card :deep(.el-tabs__header) { margin-bottom: 6px; }
}

/* 更矮的屏（≤800px，如 1366×768 最大化窗口只剩 ~738）：再收一档行距。
   实测这一档把 1366×768 从 +18px 溢出压到 0。 */
@media (max-height: 800px) {
  .simple-form :deep(.el-form-item) { margin-bottom: 2px; }
  .group-head { margin: 2px 0 0; }
}
/* 只读检索点（606 主题词 / 7xx 责任者）：放在「分类与主题」行的右半栏，两行内联，
   不再各占一块 —— 空态写"无"，因为区块的意义就是让编目员知道有这些字段可以补。 */
.ro-inline {
  display: flex; flex-direction: column; justify-content: center; gap: 2px;
  min-height: 28px; padding-left: 4px;
}
.ro-line {
  display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
  font-size: 12px; line-height: 1.5;
}
.ro-label { color: var(--app-text-muted); flex: 0 0 auto; }
.ro-empty { font-size: 12px; color: var(--el-text-color-placeholder); }
.ro-line :deep(.el-tag) { height: 20px; line-height: 18px; padding: 0 5px; }
/* `:deep(.row-inactive)`（已注销/已遗失的册置灰）随馆藏表格搬进 components/HoldingList.vue */
</style>
