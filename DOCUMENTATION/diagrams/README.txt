png/    ready-to-use images of all 13 diagrams
source/ the code that produced each one

PlantUML (.puml): install Java, download plantuml.jar from https://plantuml.com/download, then run
  java -jar plantuml.jar -tpng 01_use_case.puml
Graphviz (.dot):  dot -Tpng 02_context_dfd.dot -o 02_context_dfd.png
Gantt (.py):      pip install matplotlib   then   python 11_gantt.py
No install option: paste a .puml file into https://www.planttext.com/
