export interface ReaderRecord {
  id: number
  library_id: number
  card_no: string
  name: string
  reader_type: string
  reader_type_label: string
  grade: string | null
  class_name: string | null
  phone: string | null
  status: string
  status_label: string
  note: string | null
  created_at: string | null
}
