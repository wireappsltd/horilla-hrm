"""
urls.py

This module is used to map url path with view methods.
"""

from django.urls import path

from base.views import object_delete
from helpdesk import views
from helpdesk.models import FAQ, FAQCategory, Ticket

urlpatterns = [
    path("faq-category-view/", views.faq_category_view, name="faq-category-view"),
    path("faq-category-create/", views.faq_category_create, name="faq-category-create"),
    path(
        "faq-category-update/<int:id>/",
        views.faq_category_update,
        name="faq-category-update",
    ),
    path(
        "faq-category-delete/<int:id>/",
        views.faq_category_delete,
        name="faq-category-delete",
    ),
    path("faq-category-search/", views.faq_category_search, name="faq-category-search"),
    path(
        "faq-view/<int:obj_id>/",
        views.faq_view,
        name="faq-view",
        kwargs={"model": FAQCategory},
    ),
    path("faq-create/<int:obj_id>/", views.create_faq, name="faq-create"),
    path("faq-update/<int:obj_id>", views.faq_update, name="faq-update"),
    path("faq-search/", views.faq_search, name="faq-search"),
    path("faq-filter/<int:id>/", views.faq_filter, name="faq-filter"),
    path("faq-suggestion/", views.faq_suggestion, name="faq-suggestion"),
    path(
        "faq-delete/<int:id>/",
        views.faq_delete,
        name="faq-delete",
    ),
    path("ticket-view/", views.ticket_view, name="ticket-view"),
    path("ticket-create", views.ticket_create, name="ticket-create"),
    path("ticket-update/<int:ticket_id>", views.ticket_update, name="ticket-update"),
    path("ticket-archive/<int:ticket_id>", views.ticket_archive, name="ticket-archive"),
    path(
        "change-ticket-status/<int:ticket_id>/",
        views.change_ticket_status,
        name="change-ticket-status",
    ),
    path("ticket-delete/<int:ticket_id>", views.ticket_delete, name="ticket-delete"),
    path("ticket-filter", views.ticket_filter, name="ticket-filter"),
    path(
        "ticket-detail/<int:ticket_id>/",
        views.ticket_detail,
        name="ticket-detail",
        kwargs={"model": Ticket},
    ),
    path(
        "ticket-individual-view/<int:ticket_id>",
        views.ticket_individual_view,
        name="ticket-individual-view",
    ),
    path(
        "view-ticket-claim-request/<int:ticket_id>",
        views.view_ticket_claim_request,
        name="view-ticket-claim-request",
    ),
    path("ticket-change-tag", views.ticket_update_tag, name="ticket-change-tag"),
    path(
        "ticket-change-raised-on/<int:ticket_id>",
        views.ticket_change_raised_on,
        name="ticket-change-raised-on",
    ),
    path(
        "ticket-change-assignees/<int:ticket_id>",
        views.ticket_change_assignees,
        name="ticket-change-assignees",
    ),
    path("ticket-create-tag", views.create_tag, name="ticket-create-tag"),
    path("remove-tag", views.remove_tag, name="remove-tag"),
    path(
        "view-ticket-document/<int:doc_id>",
        views.view_ticket_document,
        name="view-ticket-document",
    ),
    path("comment-create/<int:ticket_id>", views.comment_create, name="comment-create"),
    path("comment-edit/", views.comment_edit, name="comment-edit"),
    path(
        "comment-delete/<int:comment_id>/", views.comment_delete, name="comment-delete"
    ),
    path("get-raised-on", views.get_raised_on, name="get-raised-on"),
    path("claim-ticket/<int:id>", views.claim_ticket, name="claim-ticket"),
    path(
        "approve-claim-request/<int:req_id>",
        views.approve_claim_request,
        name="approve-claim-request",
    ),
    path(
        "tickets-select-filter",
        views.tickets_select_filter,
        name="tickets-select-filter",
    ),
    path(
        "tickets-bulk-archive", views.tickets_bulk_archive, name="tickets-bulk-archive"
    ),
    path("tickets-bulk-delete", views.tickets_bulk_delete, name="tickets-bulk-delete"),
    path(
        "department-manager-create/",
        views.create_department_manager,
        name="department-manager-create",
    ),
    path(
        "department-manager-update/<int:dep_id>",
        views.update_department_manager,
        name="department-manager-update",
    ),
    path(
        "department-manager-delete/<int:dep_id>",
        views.delete_department_manager,
        name="department-manager-delete",
    ),
    path(
        "update-priority/<int:ticket_id>",
        views.update_priority,
        name="update-priority",
    ),
    path("ticket-type-view/", views.ticket_type_view, name="ticket-type-view"),
    path("ticket-type-create", views.ticket_type_create, name="ticket-type-create"),
    path(
        "ticket-type-update/<int:t_type_id>",
        views.ticket_type_update,
        name="ticket-type-update",
    ),
    path(
        "ticket-type-delete/<int:t_type_id>",
        views.ticket_type_delete,
        name="ticket-type-delete",
    ),
    path(
        "department-manager-view/",
        views.view_department_managers,
        name="department-manager-view",
    ),
    path(
        "get-department-employee",
        views.get_department_employees,
        name="get-department-employee",
    ),
    path(
        "delete-ticket-document/<int:doc_id>",
        views.delete_ticket_document,
        name="delete-ticket-document",
    ),
    path("load-faqs/", views.load_faqs, name="load-faqs"),
    path("iso-forms/", views.iso_forms_home, name="iso-forms-home"),

    # ── Password Reset Request URLs ──────────────────────────────────────────
    path(
        "password-reset-request/create/",
        views.password_reset_request_create,
        name="password-reset-request-create",
    ),
    path(
        "password-reset-request/update/<int:pr_id>/",
        views.password_reset_request_update,
        name="password-reset-request-update",
    ),
    path(
        "password-reset-request/review/<int:pr_id>/",
        views.iso_review_password_reset,
        name="iso-review-password-reset",
    ),
    path(
        "password-reset-request/mark-awaiting/<int:pr_id>/",
        views.password_reset_mark_awaiting,
        name="password-reset-mark-awaiting",
    ),
    path(
        "password-reset-request/acknowledge/<int:pr_id>/",
        views.password_reset_acknowledge,
        name="password-reset-acknowledge",
    ),
    path(
        "password-reset-request/withdraw/<int:pr_id>/",
        views.password_reset_request_withdraw,
        name="password-reset-request-withdraw",
    ),
    path(
        "password-reset-request/delete/<int:pr_id>/",
        views.password_reset_request_delete,
        name="password-reset-request-delete",
    ),

    # ── Access Request & Deactivation URLs ───────────────────────────────────
    path(
        "access-request/create/",
        views.access_request_create,
        name="access-request-create",
    ),
    path(
        "access-request/update/<int:ar_id>/",
        views.access_request_update,
        name="access-request-update",
    ),
    path(
        "access-request/dh-review/<int:ar_id>/",
        views.divisional_head_review_access_request,
        name="access-request-dh-review",
    ),
    path(
        "access-request/iso-review/<int:ar_id>/",
        views.iso_review_access_request,
        name="access-request-iso-review",
    ),
    path(
        "access-request/acknowledge/<int:ar_id>/",
        views.access_request_acknowledge,
        name="access-request-acknowledge",
    ),
    path(
        "access-request/withdraw/<int:ar_id>/",
        views.access_request_withdraw,
        name="access-request-withdraw",
    ),
    path(
        "access-request/delete/<int:ar_id>/",
        views.access_request_delete,
        name="access-request-delete",
    ),
    # ── Exception Request URLs ───────────────────────────────────────────────
    path(
        "exception-request/create/",
        views.exception_request_create,
        name="exception-request-create",
    ),
    path(
        "exception-request/update/<int:er_id>/",
        views.exception_request_update,
        name="exception-request-update",
    ),
    path(
        "exception-request/iso-review/<int:er_id>/",
        views.iso_review_exception_request,
        name="exception-request-iso-review",
    ),
    path(
        "exception-request/isc-review/<int:er_id>/",
        views.isc_review_exception_request,
        name="exception-request-isc-review",
    ),
    path(
        "exception-request/acknowledge/<int:er_id>/",
        views.exception_request_acknowledge,
        name="exception-request-acknowledge",
    ),
    path(
        "exception-request/withdraw/<int:er_id>/",
        views.exception_request_withdraw,
        name="exception-request-withdraw",
    ),
    path(
        "exception-request/delete/<int:er_id>/",
        views.exception_request_delete,
        name="exception-request-delete",
    ),
    # ── Admin Access Request URLs ────────────────────────────────────────────
    path(
        "admin-access-request/create/",
        views.admin_access_request_create,
        name="admin-access-request-create",
    ),
    path(
        "admin-access-request/update/<int:aar_id>/",
        views.admin_access_request_update,
        name="admin-access-request-update",
    ),
    path(
        "admin-access-request/iso-review/<int:aar_id>/",
        views.iso_review_admin_access_request,
        name="admin-access-request-iso-review",
    ),
    path(
        "admin-access-request/isc-review/<int:aar_id>/",
        views.isc_review_admin_access_request,
        name="admin-access-request-isc-review",
    ),
    path(
        "admin-access-request/acknowledge/<int:aar_id>/",
        views.admin_access_request_acknowledge,
        name="admin-access-request-acknowledge",
    ),
    path(
        "admin-access-request/withdraw/<int:aar_id>/",
        views.admin_access_request_withdraw,
        name="admin-access-request-withdraw",
    ),
    path(
        "admin-access-request/delete/<int:aar_id>/",
        views.admin_access_request_delete,
        name="admin-access-request-delete",
    ),
]

