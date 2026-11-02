<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import BaseModal from '../../components/BaseModal.vue'
import { errorMessage, post, put } from '../../lib/api'
import type { ReaderRecord } from '../../lib/readerTypes'
import { toast, toastError } from '../../lib/toast'

const props = defineProps<{
  open: boolean
  reader?: ReaderRecord | null
  libraryId: number | null
}>()

const emit = defineEmits<{ close: []; saved: [reader: ReaderRecord] }>()

const form = reactive({
  card_no: '',
  name: '',
  reader_type: 'student',
  grade: '',
  class_name: '',
  phone: '',
  status: 'ACTIVE',
  note: '',
})
const busy = ref(false)
const error = ref('')

function reset() {
  const reader = props.reader
  form.card_no = reader?.card_no || ''
  form.name = reader?.name || ''
  form.reader_type = reader?.reader_type || 'student'
  form.grade = reader?.grade || ''
  form.class_name = reader?.class_name || ''
  form.phone = reader?.phone || ''
  form.status = reader?.status || 'ACTIVE'
  form.note = reader?.note || ''
  error.value = ''
}

watch(
  () => [props.open, props.reader?.id],
  ([open]) => {
    if (open) reset()
  },
)

async function submit() {
  if (busy.value) return
  if (!form.card_no.trim()) {
    error.value = '请填写借书证号'
    return
  }
  if (!form.name.trim()) {
    error.value = '请填写姓名'
    return
  }
  busy.value = true
  error.value = ''
  const payload = {
    card_no: form.card_no.trim(),
    name: form.name.trim(),
    reader_type: form.reader_type,
    grade: form.grade.trim(),
    class_name: form.class_name.trim(),
    phone: form.phone.trim(),
    status: form.status,
    note: form.note.trim(),
  }
  try {
    let saved: ReaderRecord
    if (props.reader) {
      saved = await put<ReaderRecord>(`/readers/${props.reader.id}`, payload)
      toast('读者资料已保存')
    } else {
      saved = await post<ReaderRecord>('/readers', {
        library_id: props.libraryId || undefined,
        ...payload,
      })
      toast('读者已建立')
    }
    emit('saved', saved)
    emit('close')
  } catch (e) {
    error.value = errorMessage(e)
    toastError(error.value)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <BaseModal
    :open="open"
    :busy="busy"
    :title="reader ? '编辑读者' : '新增读者'"
    :subtitle="reader ? `${reader.card_no} · ${reader.name}` : '借书证号在本图书库内唯一'"
    @close="emit('close')"
  >
    <div class="form-grid">
      <div class="field">
        <label for="rf-card">借书证号 <span class="req">*</span></label>
        <input id="rf-card" v-model="form.card_no" class="input" maxlength="32" autocomplete="off">
      </div>
      <div class="field">
        <label for="rf-name">姓名 <span class="req">*</span></label>
        <input id="rf-name" v-model="form.name" class="input" maxlength="64" autocomplete="off">
      </div>
      <div class="field">
        <label for="rf-type">读者类型</label>
        <select id="rf-type" v-model="form.reader_type" class="select">
          <option value="student">学生</option>
          <option value="teacher">教师</option>
          <option value="staff">职工</option>
          <option value="other">其他</option>
        </select>
      </div>
      <div class="field">
        <label for="rf-status">状态</label>
        <select id="rf-status" v-model="form.status" class="select">
          <option value="ACTIVE">正常</option>
          <option value="SUSPENDED">暂停借阅</option>
          <option value="INACTIVE">已注销</option>
        </select>
      </div>
      <div class="field">
        <label for="rf-grade">年级</label>
        <input id="rf-grade" v-model="form.grade" class="input" maxlength="32" placeholder="如 七年级">
      </div>
      <div class="field">
        <label for="rf-class">班级 / 部门</label>
        <input id="rf-class" v-model="form.class_name" class="input" maxlength="64" placeholder="如 七年级(3)班">
      </div>
      <div class="field span-2">
        <label for="rf-phone">联系电话</label>
        <input id="rf-phone" v-model="form.phone" class="input" maxlength="32" inputmode="tel" autocomplete="off">
      </div>
      <div class="field span-2">
        <label for="rf-note">备注</label>
        <textarea id="rf-note" v-model="form.note" class="textarea" rows="3" maxlength="256" />
        <span class="field-note">可记录暂停或注销的原因。状态变更会同步取消其进行中的预约。</span>
      </div>
    </div>
    <p v-if="error" class="field-note is-error" style="margin-top: 12px" role="alert">{{ error }}</p>
    <template #footer>
      <button class="btn btn-ghost" type="button" :disabled="busy" @click="emit('close')">取消</button>
      <button class="btn btn-primary" type="button" :disabled="busy" @click="submit">
        {{ busy ? '保存中…' : reader ? '保存修改' : '建立读者' }}
      </button>
    </template>
  </BaseModal>
</template>
