"""reader accounts, reader sessions, seat reservations

Revision ID: c9e2f4a7b6d3
Revises: b7d1c4a9e3f2
Create Date: 2026-10-22 18:30:00.000000

M4：读者门户账号与会话，以及座位区域 / 座位 / 座位预约。
馆员 users 与读者账号完全分离；座位预约用部分唯一索引保证并发下不重复占座。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = 'c9e2f4a7b6d3'
down_revision: str | None = 'b7d1c4a9e3f2'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'reader_accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reader_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=32), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['reader_id'], ['readers.id'], ondelete='CASCADE',
                                name='fk_reader_accounts_reader_id_readers'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('reader_id', name='uq_reader_accounts_reader_id'),
    )
    op.create_index(op.f('ix_reader_accounts_reader_id'), 'reader_accounts',
                    ['reader_id'], unique=False)
    op.create_index(op.f('ix_reader_accounts_username'), 'reader_accounts',
                    ['username'], unique=True)

    op.create_table(
        'reader_sessions',
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('reader_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('client', sa.String(length=256), nullable=True),
        sa.ForeignKeyConstraint(['reader_id'], ['readers.id'], ondelete='CASCADE',
                                name='fk_reader_sessions_reader_id_readers'),
        sa.PrimaryKeyConstraint('token_hash'),
    )
    op.create_index(op.f('ix_reader_sessions_reader_id'), 'reader_sessions',
                    ['reader_id'], unique=False)
    op.create_index(op.f('ix_reader_sessions_expires_at'), 'reader_sessions',
                    ['expires_at'], unique=False)

    op.create_table(
        'seat_areas',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('floor', sa.String(length=32), nullable=True),
        sa.Column('description', sa.String(length=256), nullable=True),
        sa.Column('rows', sa.Integer(), nullable=False),
        sa.Column('cols', sa.Integer(), nullable=False),
        sa.Column('open_time', sa.Time(), nullable=False),
        sa.Column('close_time', sa.Time(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['library_id'], ['book_libraries.id'],
                                name='fk_seat_areas_library_id_book_libraries'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_seat_areas_library_id'), 'seat_areas',
                    ['library_id'], unique=False)

    op.create_table(
        'seats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('area_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('row', sa.Integer(), nullable=False),
        sa.Column('col', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['area_id'], ['seat_areas.id'], ondelete='CASCADE',
                                name='fk_seats_area_id_seat_areas'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('area_id', 'code', name='uq_seats_area_code'),
        sa.UniqueConstraint('area_id', 'row', 'col', name='uq_seats_area_row_col'),
    )
    op.create_index(op.f('ix_seats_area_id'), 'seats', ['area_id'], unique=False)

    op.create_table(
        'seat_bookings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('library_id', sa.Integer(), nullable=False),
        sa.Column('seat_id', sa.Integer(), nullable=False),
        sa.Column('reader_id', sa.Integer(), nullable=False),
        sa.Column('booking_date', sa.Date(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('checked_in_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['library_id'], ['book_libraries.id'],
                                name='fk_seat_bookings_library_id_book_libraries'),
        sa.ForeignKeyConstraint(['seat_id'], ['seats.id'], ondelete='CASCADE',
                                name='fk_seat_bookings_seat_id_seats'),
        sa.ForeignKeyConstraint(['reader_id'], ['readers.id'],
                                name='fk_seat_bookings_reader_id_readers'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_seat_bookings_library_id'), 'seat_bookings', ['library_id'])
    op.create_index(op.f('ix_seat_bookings_seat_id'), 'seat_bookings', ['seat_id'])
    op.create_index(op.f('ix_seat_bookings_reader_id'), 'seat_bookings', ['reader_id'])
    op.create_index(op.f('ix_seat_bookings_booking_date'), 'seat_bookings', ['booking_date'])
    op.create_index(op.f('ix_seat_bookings_status'), 'seat_bookings', ['status'])
    op.create_index('uq_seat_bookings_active_slot', 'seat_bookings',
                    ['seat_id', 'booking_date', 'start_time'], unique=True,
                    postgresql_where=sa.text("status IN ('BOOKED', 'CHECKED_IN')"))
    op.create_index('uq_seat_bookings_reader_day', 'seat_bookings',
                    ['reader_id', 'booking_date'], unique=True,
                    postgresql_where=sa.text("status IN ('BOOKED', 'CHECKED_IN')"))
    op.create_index('ix_seat_bookings_library_date', 'seat_bookings',
                    ['library_id', 'booking_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_seat_bookings_library_date', table_name='seat_bookings')
    op.drop_index('uq_seat_bookings_reader_day', table_name='seat_bookings',
                  postgresql_where=sa.text("status IN ('BOOKED', 'CHECKED_IN')"))
    op.drop_index('uq_seat_bookings_active_slot', table_name='seat_bookings',
                  postgresql_where=sa.text("status IN ('BOOKED', 'CHECKED_IN')"))
    op.drop_index(op.f('ix_seat_bookings_status'), table_name='seat_bookings')
    op.drop_index(op.f('ix_seat_bookings_booking_date'), table_name='seat_bookings')
    op.drop_index(op.f('ix_seat_bookings_reader_id'), table_name='seat_bookings')
    op.drop_index(op.f('ix_seat_bookings_seat_id'), table_name='seat_bookings')
    op.drop_index(op.f('ix_seat_bookings_library_id'), table_name='seat_bookings')
    op.drop_table('seat_bookings')
    op.drop_index(op.f('ix_seats_area_id'), table_name='seats')
    op.drop_table('seats')
    op.drop_index(op.f('ix_seat_areas_library_id'), table_name='seat_areas')
    op.drop_table('seat_areas')
    op.drop_index(op.f('ix_reader_sessions_expires_at'), table_name='reader_sessions')
    op.drop_index(op.f('ix_reader_sessions_reader_id'), table_name='reader_sessions')
    op.drop_table('reader_sessions')
    op.drop_index(op.f('ix_reader_accounts_username'), table_name='reader_accounts')
    op.drop_index(op.f('ix_reader_accounts_reader_id'), table_name='reader_accounts')
    op.drop_table('reader_accounts')
