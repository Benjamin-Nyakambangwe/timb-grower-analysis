from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

class Grower(models.Model):
    grower_number = models.CharField(max_length=20, unique=True, primary_key=True, help_text="Unique Grower ID, e.g., V100081")
    name = models.CharField(max_length=100)
    surname = models.CharField(max_length=100)
    national_id = models.CharField(max_length=50, unique=True, blank=True, null=True)
    farming_province = models.CharField(max_length=100, blank=True)
    farm_name = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    registered_since = models.DateField(null=True, blank=True)
    first_sales_year = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['surname', 'name']
        verbose_name = "Grower"
        verbose_name_plural = "Growers"

    def __str__(self):
        return f"{self.name} {self.surname} ({self.grower_number})"

class Contractor(models.Model):
    name = models.CharField(max_length=255, unique=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = "Contractor"
        verbose_name_plural = "Contractors"
    
    def __str__(self):
        return self.name

class Creditor(models.Model):
    name = models.CharField(max_length=255, unique=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = "Creditor"
        verbose_name_plural = "Creditors"
    
    def __str__(self):
        return self.name

class SeasonalReport(models.Model):
    grower = models.ForeignKey(Grower, on_delete=models.CASCADE, related_name='seasonal_reports')
    season_year = models.PositiveIntegerField(help_text="The year of the season, e.g., 2024")
    
    # Must know information (seasonal)
    contractor = models.ForeignKey(Contractor, on_delete=models.SET_NULL, null=True, blank=True, related_name='seasonal_reports')
    estimated_dry_mass = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_dry_hectrage = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_irrigated_mass = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    estimated_irrigated_hectrage = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    # Sales Summary
    total_bales = models.PositiveIntegerField(null=True, blank=True)
    total_mass_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    total_value_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    average_price_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        # A grower can only have one report per year.
        unique_together = ('grower', 'season_year')
        ordering = ['-season_year']
        verbose_name = "Seasonal Report"
        verbose_name_plural = "Seasonal Reports"

    def __str__(self):
        return f"Report for {self.grower.grower_number} - {self.season_year}"

class GradeAnalysis(models.Model):
    seasonal_report = models.ForeignKey(SeasonalReport, on_delete=models.CASCADE, related_name='grade_analysis_items')
    grade_name = models.CharField(max_length=100)
    mass_kg = models.DecimalField(max_digits=10, decimal_places=2)
    value_usd = models.DecimalField(max_digits=12, decimal_places=2)
    average_price_usd = models.DecimalField(max_digits=10, decimal_places=2)
    
    class Meta:
        verbose_name = "Grade Analysis"
        verbose_name_plural = "Grade Analyses"

    def __str__(self):
        return f"{self.grade_name} for Report {self.seasonal_report.id}"

class CreditorRecovery(models.Model):
    seasonal_report = models.ForeignKey(SeasonalReport, on_delete=models.CASCADE, related_name='creditor_recoveries')
    creditor = models.ForeignKey(Creditor, on_delete=models.CASCADE, related_name='recoveries')
    total_owed_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_paid_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True )
    recovery_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        null=True, 
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Recovery percentage, e.g., 49.57"
    )

    class Meta:
        verbose_name = "Creditor Recovery"
        verbose_name_plural = "Creditor Recoveries"

    def __str__(self):
        return f"{self.creditor.name} for Report {self.seasonal_report.id}"