"""Notification copy per event, in the three supported languages (ar / fr / en).

Keys are event names; booking events carry the resulting status. Falls back to
French when a language or key is missing — fr is the platform default.
"""

_TITLES = {
    "verification.changed": {
        "APPROVED": {"fr": "Document approuvé", "en": "Document approved", "ar": "تمت الموافقة على الوثيقة"},
        "REJECTED": {"fr": "Document refusé", "en": "Document rejected", "ar": "تم رفض الوثيقة"},
        "NEEDS_MORE_DOCUMENTS": {"fr": "Documents supplémentaires requis", "en": "More documents needed", "ar": "مطلوب وثائق إضافية"},
    },
    "ride.reminder": {"fr": "Départ dans une heure", "en": "Departure within an hour", "ar": "المغادرة خلال ساعة"},
    "ride.cancelled": {"fr": "Trajet annulé", "en": "Ride cancelled", "ar": "تم إلغاء الرحلة"},
    "payment.status_changed": {
        "PAID": {"fr": "Paiement reçu", "en": "Payment received", "ar": "تم استلام الدفع"},
        "FAILED": {"fr": "Paiement échoué", "en": "Payment failed", "ar": "فشل الدفع"},
        "CANCELLED": {"fr": "Paiement annulé", "en": "Payment cancelled", "ar": "تم إلغاء الدفع"},
    },
    "wallet.posted": {"fr": "Mouvement du portefeuille", "en": "Wallet activity", "ar": "حركة المحفظة"},
    "incentive.awarded": {"fr": "Récompense disponible", "en": "Reward available", "ar": "مكافأة متاحة"},
    "ride.published": {"fr": "Nouveau trajet", "en": "New ride", "ar": "رحلة جديدة"},
    "match.found": {"fr": "Trajet correspondant", "en": "Ride match", "ar": "رحلة مطابقة"},
    "booking.created": {"fr": "Nouvelle demande de réservation", "en": "New booking request", "ar": "طلب حجز جديد"},
    "booking.status_changed": {
        "ACCEPTED": {"fr": "Réservation acceptée", "en": "Booking accepted", "ar": "تم قبول الحجز"},
        "CONFIRMED": {"fr": "Réservation confirmée", "en": "Booking confirmed", "ar": "تم تأكيد الحجز"},
        "COMPLETED": {"fr": "Trajet terminé", "en": "Ride completed", "ar": "انتهت الرحلة"},
        "REJECTED": {"fr": "Réservation refusée", "en": "Booking rejected", "ar": "تم رفض الحجز"},
        "CANCELLED_BY_PASSENGER": {"fr": "Réservation annulée", "en": "Booking cancelled", "ar": "تم إلغاء الحجز"},
        "CANCELLED_BY_DRIVER": {"fr": "Trajet annulé par le chauffeur", "en": "Ride cancelled by the driver", "ar": "ألغى السائق الرحلة"},
        "NO_SHOW": {"fr": "Passager absent", "en": "Passenger no-show", "ar": "غائب عن الرحلة"},
    },
}

_BODIES = {
    "verification.changed": {
        "APPROVED": {"fr": "Consultez votre vérification pour voir la prochaine étape.", "en": "Open verification to see your next step.", "ar": "افتح التحقق لمعرفة الخطوة التالية."},
        "REJECTED": {"fr": "Consultez le motif dans votre vérification avant de réessayer.", "en": "Review the reason in verification before trying again.", "ar": "راجع السبب في التحقق قبل المحاولة مجددًا."},
        "NEEDS_MORE_DOCUMENTS": {"fr": "Consultez votre vérification et fournissez les documents demandés.", "en": "Open verification and provide the requested documents.", "ar": "افتح التحقق وقدم الوثائق المطلوبة."},
    },
    "ride.reminder": {"fr": "Votre trajet {origin} → {destination} part à {departure_time}.", "en": "Your ride {origin} → {destination} leaves at {departure_time}.", "ar": "رحلتك {origin} ← {destination} تغادر الساعة {departure_time}."},
    "ride.cancelled": {"fr": "Ce trajet a été annulé. Consultez les réservations pour les remboursements enregistrés.", "en": "This ride was cancelled. Open bookings for recorded refunds.", "ar": "تم إلغاء الرحلة. افتح الحجوزات لمراجعة المبالغ المستردة المسجلة."},
    "payment.status_changed": {
        "PAID": {"fr": "{amount} TND ont été crédités à votre portefeuille.", "en": "{amount} TND was credited to your wallet.", "ar": "تمت إضافة {amount} دينار إلى محفظتك."},
        "FAILED": {"fr": "Le paiement n'est pas confirmé. Vérifiez son état dans le portefeuille avant de réessayer.", "en": "Payment is not confirmed. Check its status in your wallet before retrying.", "ar": "الدفع غير مؤكد. تحقق من حالته في محفظتك قبل المحاولة مجددًا."},
        "CANCELLED": {"fr": "Paiement annulé. Un paiement confirmé tardivement sera crédité.", "en": "Payment cancelled. A late confirmed payment will still be credited.", "ar": "تم إلغاء الدفع. سيضاف أي دفع مؤكد لاحقًا إلى المحفظة."},
    },
    "wallet.posted": {"fr": "{amount} TND — {description}. Consultez votre portefeuille.", "en": "{amount} TND — {description}. Open your wallet for details.", "ar": "{amount} دينار — {description}. افتح محفظتك للتفاصيل."},
    "incentive.awarded": {"fr": "Récompense de {amount} TND disponible jusqu'au {expires_at}. Consultez ses conditions dans le portefeuille.", "en": "A {amount} TND reward is available until {expires_at}. Review its terms in your wallet.", "ar": "مكافأة بقيمة {amount} دينار متاحة حتى {expires_at}. راجع شروطها في محفظتك."},
    "ride.published": {"fr": "Un nouveau trajet vient d'être publié.", "en": "A new ride was just published.", "ar": "تم نشر رحلة جديدة."},
    "match.found": {"fr": "Un trajet correspond à votre recherche.", "en": "A ride matches your interest.", "ar": "تتوافق رحلة مع طلبك."},
    "booking.created": {"fr": "Un passager demande une place sur votre trajet.", "en": "A passenger asked for a seat on your ride.", "ar": "طلب راكب مقعدًا في رحلتك."},
    "booking.status_changed": {
        "ACCEPTED": {"fr": "Réservation acceptée. Le passager peut vérifier et payer pour confirmer.", "en": "Booking accepted. The passenger can review and pay to confirm.", "ar": "تم قبول الحجز. يمكن للراكب المراجعة والدفع لتأكيده."},
        "CONFIRMED": {"fr": "Votre réservation est confirmée.", "en": "Your booking is confirmed.", "ar": "تم تأكيد حجزك."},
        "COMPLETED": {"fr": "Trajet terminé. Vous pouvez évaluer l'autre participant.", "en": "Ride completed. You can review the other participant.", "ar": "انتهت الرحلة. يمكنك تقييم المشارك الآخر."},
        "REJECTED": {"fr": "Le chauffeur a refusé votre réservation.", "en": "The driver rejected your booking.", "ar": "رفض السائق حجزك."},
        "CANCELLED_BY_PASSENGER": {"fr": "Le passager a annulé la réservation. Remboursement au passager : {refund} TND.", "en": "The passenger cancelled the booking. Passenger wallet refund: {refund} TND.", "ar": "ألغى الراكب الحجز. المبلغ المسترد لمحفظة الراكب: {refund} دينار."},
        "CANCELLED_BY_DRIVER": {"fr": "Le chauffeur a annulé la réservation. Remboursement au passager : {refund} TND.", "en": "The driver cancelled the booking. Passenger wallet refund: {refund} TND.", "ar": "ألغى السائق الحجز. المبلغ المسترد لمحفظة الراكب: {refund} دينار."},
        "NO_SHOW": {"fr": "Le passager ne s'est pas présenté.", "en": "The passenger did not show up.", "ar": "لم يحضر الراكب."},
    },
}

_DEFAULT_LANGUAGE = "fr"


def render_notification(*, kind: str, status: str | None = None, preferred_language: str = _DEFAULT_LANGUAGE, payload=None):
    titles = _TITLES.get(kind, {})
    bodies = _BODIES.get(kind, {})
    if status is not None:
        titles, bodies = titles.get(status, {}), bodies.get(status, {})
    lang = preferred_language if preferred_language in titles else _DEFAULT_LANGUAGE
    return titles.get(lang, ""), bodies.get(lang, "").format(**(payload or {}))[:500]
