// Multiplier-Free Ternary Processing Element
// Weights: 2'b01 = +1, 2'b10 = -1, 2'b00 = 0
module bitnet_ternary_pe #(
    parameter DATA_WIDTH = 8,
    parameter ACC_WIDTH  = 16
)(
    input  wire                  clk,
    input  wire                  rst_n,
    input  wire                  enable,
    input  wire [DATA_WIDTH-1:0] in_data,
    input  wire [1:0]            in_weight, // 2'b01 (+1), 2'b10 (-1), 2'b00 (0)
    input  wire [ACC_WIDTH-1:0]  acc_in,
    output reg  [ACC_WIDTH-1:0]  acc_out
);

    wire signed [DATA_WIDTH-1:0] signed_data = in_data;
    reg  signed [ACC_WIDTH-1:0]  term;

    always @(*) begin
        case (in_weight)
            2'b01:   term = {{ (ACC_WIDTH-DATA_WIDTH){signed_data[DATA_WIDTH-1]} }, signed_data};  // +1
            2'b10:   term = -({{ (ACC_WIDTH-DATA_WIDTH){signed_data[DATA_WIDTH-1]} }, signed_data}); // -1
            default: term = {ACC_WIDTH{1'b0}};                                                       //  0
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acc_out <= {ACC_WIDTH{1'b0}};
        end else if (enable) begin
            acc_out <= acc_in + term;
        end
    end

endmodule
