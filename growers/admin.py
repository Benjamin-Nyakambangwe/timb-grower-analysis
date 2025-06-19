from django.contrib import admin
from .models import Grower, Contractor, Creditor, SeasonalReport, GradeAnalysis, CreditorRecovery


@admin.register(Grower)
class GrowerAdmin(admin.ModelAdmin):
    list_display = ('grower_number', 'name', 'surname', 'farming_province', 'farm_name')
    list_filter = ('farming_province',)
    search_fields = ('grower_number', 'name', 'surname', 'farm_name')


@admin.register(Contractor)
class ContractorAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(Creditor)
class CreditorAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)


@admin.register(SeasonalReport)
class SeasonalReportAdmin(admin.ModelAdmin):
    list_display = ('grower', 'season_year', 'contractor', 'total_value_usd', 'total_mass_kg')
    list_filter = ('season_year', 'contractor')
    search_fields = ('grower__grower_number', 'grower__name', 'contractor__name')


@admin.register(GradeAnalysis)
class GradeAnalysisAdmin(admin.ModelAdmin):
    list_display = ('seasonal_report', 'grade_name', 'mass_kg', 'value_usd')
    list_filter = ('grade_name',)
    search_fields = ('grade_name', 'seasonal_report__grower__grower_number')


@admin.register(CreditorRecovery)
class CreditorRecoveryAdmin(admin.ModelAdmin):
    list_display = ('seasonal_report', 'creditor', 'total_owed_usd', 'recovery_percentage')
    list_filter = ('creditor',)
    search_fields = ('creditor__name', 'seasonal_report__grower__grower_number')


# Customize admin site header
admin.site.site_header = "T.I.M.B Grower Analysis System"
admin.site.site_title = "T.I.M.B Admin"
admin.site.index_title = "Grower Analysis Administration"





