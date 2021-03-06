# Copyright 2018 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import base64
import pathlib
from io import BytesIO

from ..utils import dict_to_json
from .base import Artifact


class PlotArtifact(Artifact):
    kind = 'plot'

    def _post_init(self):
        self.viewer = 'chart'
        import matplotlib
        if not self._body or not isinstance(
           self._body, matplotlib.figure.Figure):
            raise ValueError(
                'matplotlib fig must be provided as artifact body')
        if not pathlib.Path(self.key).suffix:
            self.format = 'html'

    def get_body(self):
        """ Convert Matplotlib figure 'fig' into a <img> tag for HTML use
        using base64 encoding. """
        from matplotlib.backends.backend_agg import \
            FigureCanvasAgg as FigureCanvas

        canvas = FigureCanvas(self._body)
        png_output = BytesIO()
        canvas.print_png(png_output)
        data = png_output.getvalue()

        data_uri = base64.b64encode(data).decode('utf-8')
        return '<img title="{}" src="data:image/png;base64,{}">'.format(
            self.key, data_uri)


chart_template = '''
<html>
  <head>
    <script
        type="text/javascript"
        src="https://www.gstatic.com/charts/loader.js"></script>
    <script type="text/javascript">
      google.charts.load('current', {'packages':['corechart']});
      google.charts.setOnLoadCallback(drawChart);
      function drawChart() {
        var data = google.visualization.arrayToDataTable($data$);
        var options = $opts$;
        var chart = new google.visualization.$chart$(
            document.getElementById('chart_div'));
        chart.draw(data, options);
      }
    </script>
  </head>
  <body>
    <div id="chart_div" style="width: 100%; height: 500px;"></div>
  </body>
</html>
'''


class ChartArtifact(Artifact):
    kind = 'chart'
    _dict_fields = [
        'key', 'kind', 'iter', 'tree', 'src_path', 'target_path', 'hash',
        'description', 'viewer',
    ]

    def __init__(self, key=None, data=None, header=None, options=None,
                 title=None, chart=None, target_path=None):
        data = [] if data is None else data
        options = {} if options is None else options
        super().__init__(key, target_path=target_path)
        self.viewer = 'chart'
        self.header = header or []
        self.title = title
        self.rows = []
        if data:
            if header:
                self.rows = data
            else:
                self.header = data[0]
                self.rows = data[1:]
        self.options = options
        self.chart = chart or 'LineChart'
        self.format = 'html'

    def add_row(self, row):
        self.rows += [row]

    def get_body(self):
        if not self.options.get('title'):
            self.options['title'] = self.title or self.key
        data = [self.header] + self.rows
        return chart_template.replace('$data$', dict_to_json(data))\
            .replace('$opts$', dict_to_json(self.options))\
            .replace('$chart$', self.chart)
