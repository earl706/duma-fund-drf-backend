"""Derived balance and starting-balance update."""

from decimal import Decimal

from django.db.models import Sum, Value
from django.db.models.functions import Coalesce
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsEmailVerified

from .models import Transaction
from .seeds import ensure_finance_ready, ensure_user_finance
from .serializers import StartingBalanceSerializer


def compute_balance(user):
    finance = ensure_user_finance(user)
    starting = finance.starting_balance or Decimal("0.00")

    def typed_sum(txn_type):
        return Transaction.objects.filter(owner=user, type=txn_type).aggregate(
            total=Coalesce(Sum("amount"), Value(Decimal("0.00")))
        )["total"]

    totals = {
        "income": typed_sum("income"),
        "expense": typed_sum("expense"),
        "transfer_in": typed_sum("transfer_in"),
        "transfer_out": typed_sum("transfer_out"),
    }
    balance = (
        starting
        + totals["income"]
        - totals["expense"]
        + totals["transfer_in"]
        - totals["transfer_out"]
    )
    return {
        "starting_balance": starting,
        "balance": balance,
        "totals": totals,
    }


class BalanceView(APIView):
    """GET derived balance; PATCH starting_balance only."""

    permission_classes = [IsAuthenticated, IsEmailVerified]

    def get(self, request):
        ensure_finance_ready(request.user)
        data = compute_balance(request.user)
        # Serialize decimals as strings for JSON stability
        return Response(
            {
                "starting_balance": str(data["starting_balance"]),
                "balance": str(data["balance"]),
                "totals": {k: str(v) for k, v in data["totals"].items()},
            }
        )

    def patch(self, request):
        ensure_finance_ready(request.user)
        finance = ensure_user_finance(request.user)
        ser = StartingBalanceSerializer(finance, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return self.get(request)
