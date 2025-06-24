from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Grower, SeasonalReport

def grower_list(request):
    """
    View to display a paginated list of all growers
    """
    growers = Grower.objects.all().order_by('surname', 'name')
    
    # Add search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        growers = growers.filter(
            Q(name__icontains=search_query) |
            Q(surname__icontains=search_query) |
            Q(grower_number__icontains=search_query) |
            Q(farming_province__icontains=search_query) |
            Q(farm_name__icontains=search_query)
        )
    
    # Pagination
    paginator = Paginator(growers, 25)  # Show 25 growers per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'total_growers': growers.count(),
    }
    
    return render(request, 'growers/grower_list.html', context)

def grower_detail(request, grower_number):
    """
    View to display detailed information about a specific grower with yearly reports in tabs
    """
    grower = get_object_or_404(Grower, grower_number=grower_number)
    
    # Get all reports for this grower organized by year
    reports = {}
    years = range(2018, 2027)  # 2018 to 2026
    
    for year in years:
        try:
            report = SeasonalReport.objects.get(grower=grower, season_year=year)
            reports[year] = {
                'report': report,
                'grade_analysis': report.grade_analysis_items.all(),
                'creditor_recoveries': report.creditor_recoveries.all()
            }
        except SeasonalReport.DoesNotExist:
            reports[year] = None
    
    # Get the active tab year from query params, default to current year or first available
    active_year = request.GET.get('year')
    if active_year:
        try:
            active_year = int(active_year)
            if active_year not in years:
                active_year = None
        except ValueError:
            active_year = None
    
    # If no valid active year, find first year with data or default to 2024
    if not active_year:
        for year in reversed(years):  # Start from most recent
            if reports[year]:
                active_year = year
                break
        if not active_year:
            active_year = 2024
    
    # Get the active report directly
    active_report = reports.get(active_year)
    
    context = {
        'grower': grower,
        'reports': reports,
        'years': years,
        'active_year': active_year,
        'active_report': active_report,
    }
    
    return render(request, 'growers/grower_detail.html', context)
